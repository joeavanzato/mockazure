$ErrorActionPreference = 'Stop'
$config = Get-Content /state/client.json -Raw | ConvertFrom-Json
$secret = (Get-Content $config.secret_file -Raw | ConvertFrom-Json).secret
$env:SSL_CERT_FILE = $config.ca_file
$result = [ordered]@{client='ExchangeOnlineManagement'; version='3.11.0'; connected=$false; checks=@()}
function Assert-Mail($condition, $message) { if (-not $condition) { throw $message } }
try {
    [System.Reflection.Assembly]::LoadFrom("/vendor/dependencies/Microsoft.Extensions.DependencyInjection.Abstractions/8.0.0/lib/net8.0/Microsoft.Extensions.DependencyInjection.Abstractions.dll") | Out-Null
    Import-Module ExchangeOnlineManagement -RequiredVersion 3.11.0
    $token = Invoke-RestMethod -Method POST -Uri "$($config.endpoints.login)/$($config.tenant_id)/oauth2/v2.0/token" -Body @{
        grant_type='client_credentials'; client_id=$config.client_id; client_secret=$secret
        scope='https://outlook.office365.com/.default'
    }
    Connect-ExchangeOnline -AccessToken $token.access_token -AppId $config.client_id -Organization $config.tenant_domain -ConnectionUri $config.endpoints.exchange -AzureADAuthorizationEndpointUri "$($config.endpoints.login)/organizations" -ShowBanner:$false -SkipLoadingFormatData -SkipLoadingCmdletHelp -DisableWAM -EnableErrorReporting:$false -LogDirectoryPath /state/exo-logs
    $result.connected = $true
    foreach ($name in @('Get-EXOMailbox','Get-EXOMailboxStatistics','Get-EXOMailboxFolderStatistics','Get-EXOCasMailbox','ArchiveStatistics','CommandLifecycle')) {
        $check = [ordered]@{name=$name; passed=$false}
        try {
            switch ($name) {
                'Get-EXOMailbox' {
                    $rows = @(Get-EXOMailbox -Identity $config.mailbox_upn -ErrorAction Stop)
                    Assert-Mail ($rows.Count -eq 1 -and $rows[0].ExternalDirectoryObjectId -eq $config.user_id -and $rows[0].ArchiveStatus -eq 'Active') 'Mailbox identity/archive mismatch'
                }
                'Get-EXOMailboxStatistics' {
                    $rows = @(Get-EXOMailboxStatistics -Identity $config.mailbox_upn -ErrorAction Stop)
                    Assert-Mail ($rows.Count -eq 1 -and $rows[0].ItemCount -eq 14 -and $rows[0].TotalItemSize.Value.ToBytes() -eq 5120) 'Mailbox statistics mismatch'
                }
                'Get-EXOMailboxFolderStatistics' {
                    $rows = @(Get-EXOMailboxFolderStatistics -Identity $config.mailbox_upn -ErrorAction Stop)
                    Assert-Mail ($rows.Count -eq 2 -and ($rows | Measure-Object ItemsInFolder -Sum).Sum -eq 14) 'Mailbox folder count mismatch'
                    Assert-Mail (@($rows | Where-Object { $_.Name -eq 'Inbox' -and $_.ItemsInFolder -eq 12 -and [string]$_.FolderSize -match '4,096 bytes' }).Count -eq 1) 'Inbox size mismatch'
                }
                'Get-EXOCasMailbox' {
                    $rows = @(Get-EXOCasMailbox -Identity $config.mailbox_upn -ErrorAction Stop)
                    Assert-Mail ($rows.Count -eq 1 -and $rows[0].ExternalDirectoryObjectId -eq $config.user_id -and $rows[0].ImapEnabled -eq $true) 'CAS identity/settings mismatch'
                }
                'ArchiveStatistics' {
                    $rows = @(Get-EXOMailboxStatistics -Identity $config.mailbox_upn -Archive -ErrorAction Stop)
                    Assert-Mail ($rows.Count -eq 1 -and $rows[0].ItemCount -eq 3 -and $rows[0].IsArchiveMailbox -eq $true -and $rows[0].TotalItemSize.Value.ToBytes() -eq 2048) 'Archive statistics mismatch'
                }
                'CommandLifecycle' {
                    $before = Get-Mailbox -Identity $config.mailbox_upn
                    Set-Mailbox -Identity $config.mailbox_upn -ForwardingSmtpAddress 'smtp:external@example.test'
                    Assert-Mail ((Get-Mailbox -Identity $config.mailbox_upn).ForwardingSmtpAddress -eq 'smtp:external@example.test') 'Forwarding write mismatch'
                    Set-Mailbox -Identity $config.mailbox_upn -ForwardingSmtpAddress $null
                    Assert-Mail ($null -eq (Get-Mailbox -Identity $config.mailbox_upn).ForwardingSmtpAddress) 'Forwarding null mismatch'
                    $rule = New-InboxRule -Mailbox $config.mailbox_upn -Name 'Module qualification' -ForwardTo 'external@example.test' -StopProcessingRules $true
                    Disable-InboxRule -Mailbox $config.mailbox_upn -Identity $rule.Identity -Confirm:$false
                    Assert-Mail ((Get-InboxRule -Mailbox $config.mailbox_upn -Identity $rule.Identity).Enabled -eq $false) 'Disable rule mismatch'
                    Enable-InboxRule -Mailbox $config.mailbox_upn -Identity $rule.Identity -Confirm:$false
                    Set-InboxRule -Mailbox $config.mailbox_upn -Identity $rule.Identity -Name 'Renamed qualification'
                    Assert-Mail ((Get-InboxRule -Mailbox $config.mailbox_upn -Identity $rule.Identity).Name -eq 'Renamed qualification') 'Rename rule mismatch'
                    Remove-InboxRule -Mailbox $config.mailbox_upn -Identity $rule.Identity -Confirm:$false
                    Assert-Mail (@(Get-InboxRule -Mailbox $config.mailbox_upn | Where-Object { $_.RuleIdentity -eq $rule.RuleIdentity }).Count -eq 0) 'Rule deletion mismatch'
                    Set-Mailbox -Identity $config.mailbox_upn -ForwardingSmtpAddress $before.ForwardingSmtpAddress
                    $rows = @()
                }
            }
            $check.passed = $true
            $check.count = $rows.Count
            $rows | ConvertTo-Json -Depth 12 | Set-Content "/state/mail-$name.json"
        } catch {
            $check.error_message = ([string]$_.Exception.Message).Replace([string]$token.access_token, "[token]").Replace([string]$secret, "[secret]")
            $check.error_type = $_.Exception.GetType().FullName
            $check.error_category = [string]$_.CategoryInfo.Category
        }
        $result.checks += $check
        $check | ConvertTo-Json -Depth 4
    }
} catch {
    $result.error_type = $_.Exception.GetType().FullName
    $result.error_category = [string]$_.CategoryInfo.Category
}
$result | ConvertTo-Json -Depth 5 | Set-Content /state/exchange-mail.json
$result | ConvertTo-Json -Depth 5
if (-not $result.connected -or @($result.checks | Where-Object { -not $_.passed }).Count) { exit 1 }

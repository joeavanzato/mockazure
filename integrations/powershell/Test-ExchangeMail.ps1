$ErrorActionPreference = 'Stop'
$config = Get-Content /state/client.json -Raw | ConvertFrom-Json
$secret = (Get-Content $config.secret_file -Raw | ConvertFrom-Json).secret
$env:SSL_CERT_FILE = $config.ca_file
$report = [ordered]@{passed=$false;version='3.11.0';modes=[ordered]@{}}
foreach($mode in @('application','delegated')) {
$result = [ordered]@{connected=$false;checks=@()}
function Assert-Mail($condition, $message) { if (-not $condition) { throw $message } }
try {
    [System.Reflection.Assembly]::LoadFrom("/vendor/dependencies/Microsoft.Extensions.DependencyInjection.Abstractions/8.0.0/lib/net8.0/Microsoft.Extensions.DependencyInjection.Abstractions.dll") | Out-Null
    Import-Module ExchangeOnlineManagement -RequiredVersion 3.11.0
    if($mode -eq 'application') {
    $token = Invoke-RestMethod -Method POST -Uri "$($config.endpoints.login)/$($config.tenant_id)/oauth2/v2.0/token" -Body @{
        grant_type='client_credentials'; client_id=$config.client_id; client_secret=$secret
        scope='https://outlook.office365.com/.default'
    }
    } else { $token = Get-Content /state/exchange-delegated-token.json -Raw | ConvertFrom-Json }
    $connect=@{
        AccessToken=$token.access_token;ConnectionUri=$config.endpoints.exchange
        AzureADAuthorizationEndpointUri="$($config.endpoints.login)/organizations"
        ShowBanner=$false;SkipLoadingFormatData=$true;SkipLoadingCmdletHelp=$true
        DisableWAM=$true;EnableErrorReporting=$false;LogDirectoryPath="/state/exo-logs"
    }
    if($mode -eq 'application'){$connect.AppId=$config.client_id;$connect.Organization=$config.tenant_domain}
    else{$connect.UserPrincipalName=$config.mailbox_upn}
    Connect-ExchangeOnline @connect

    $result.connected = $true
    foreach ($name in @('Get-EXOMailbox','Get-EXOMailboxStatistics','Get-EXOMailboxFolderStatistics','Get-EXOCasMailbox','ArchiveStatistics','CommandLifecycle','TraceAndDetail')) {
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
                'TraceAndDetail' {
                    $start=[datetime]::Parse($config.mail_fixture.trace_start).ToUniversalTime()
                    $end=[datetime]::Parse($config.mail_fixture.trace_end).ToUniversalTime()
                    $first=@(Get-MessageTraceV2 -StartDate $start -EndDate $end -ResultSize 5000)
                    Assert-Mail ($first.Count -eq 5000) 'Trace first page mismatch'
                    $last=$first[-1]
                    $second=@(Get-MessageTraceV2 -StartDate $start -EndDate ([datetime]$last.Received) -StartingRecipientAddress $last.RecipientAddress -ResultSize 5000)
                    $all=@($first)+@($second)
                    Assert-Mail ($all.Count -eq 5003 -and @($all.MessageTraceId | Sort-Object -Unique).Count -eq 5003) 'Trace continuation mismatch'
                    Assert-Mail (@($all | Where-Object {$_.MessageTraceId -notin $config.mail_fixture.trace_ids}).Count -eq 0) 'Trace identities mismatch'
                    $detail=@(Get-MessageTraceDetailV2 -MessageTraceId $first[0].MessageTraceId -RecipientAddress $config.mailbox_upn -StartDate $start -EndDate $end)
                    Assert-Mail ($detail.Count -eq 1 -and $detail[0].Event -eq 'DELIVER' -and $detail[0].Action -eq 'Delivered') 'Trace detail mismatch'
                    $rows=$detail
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
            $rows | ConvertTo-Json -Depth 12 | Set-Content "/state/mail-$mode-$name.json"
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
if ($result.connected) { Disconnect-ExchangeOnline -Confirm:$false | Out-Null }
if (-not $result.connected -or @($result.checks | Where-Object { -not $_.passed }).Count) {
    $result | ConvertTo-Json -Depth 5
    exit 1
}
$checks=[ordered]@{}
foreach($check in $result.checks){$checks[$check.name]=$check.passed}
$report.modes[$mode]=$checks
}
$report.passed=$true
$report | ConvertTo-Json -Depth 10 | Set-Content /state/exchange-mail.json
$report | ConvertTo-Json -Depth 10

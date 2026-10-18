$ErrorActionPreference='Stop'
$config=Get-Content /state/client.json -Raw | ConvertFrom-Json
$secret=(Get-Content $config.secret_file -Raw | ConvertFrom-Json).secret
$env:SSL_CERT_FILE=$config.ca_file
[Reflection.Assembly]::LoadFrom('/vendor/dependencies/Microsoft.Extensions.DependencyInjection.Abstractions/8.0.0/lib/net8.0/Microsoft.Extensions.DependencyInjection.Abstractions.dll') | Out-Null
Import-Module ExchangeOnlineManagement -RequiredVersion 3.11.0
$token=Invoke-RestMethod -Method POST -Uri "$($config.endpoints.login)/$($config.tenant_id)/oauth2/v2.0/token" -Body @{
 grant_type='client_credentials';client_id=$config.client_id;client_secret=$secret;scope='https://outlook.office365.com/.default'
}
try {
 Connect-ExchangeOnline -AccessToken $token.access_token -ConnectionUri $config.endpoints.exchange -AzureADAuthorizationEndpointUri "$($config.endpoints.login)/organizations" -AppId $config.client_id -Organization $config.tenant_domain -ShowBanner:$false -SkipLoadingFormatData -SkipLoadingCmdletHelp -DisableWAM -EnableErrorReporting:$false -LogDirectoryPath /state/exo-logs
} catch {
 $cause=$_.Exception
 while($cause){Write-Output ($cause.GetType().Name+': '+$cause.Message);$cause=$cause.InnerException}
 throw
}
try {
 $org=Get-OrganizationConfig
 if($org.AuditDisabled -ne $true){throw 'Organization configuration mismatch'}
 $transport=Get-TransportConfig
 if($transport.SmtpClientAuthenticationDisabled -ne $true){throw 'Transport configuration mismatch'}
 $domains=@(Get-AcceptedDomain -ResultSize Unlimited)
 if($domains.Count -ne 1 -or $domains[0].DomainName -ne $config.tenant_domain){throw 'Accepted domain mismatch'}
 $rules=@(Get-TransportRule -State Enabled -ResultSize Unlimited)
 if($rules.Count -ne 1 -or $rules[0].Name -ne 'Forwarding review' -or $rules[0].Mode -ne 'Audit'){throw 'Transport rule mismatch'}
 $permissions=@(Get-MailboxPermission -Identity $config.mailbox_upn)
 if($permissions.Count -ne 2 -or @($permissions | Where-Object {$_.Deny -and $_.IsInherited}).Count -ne 1){throw 'Mailbox permission evidence mismatch'}
 $recipient=@(Get-RecipientPermission -Identity $config.mailbox_upn -AccessRights SendAs)
 if($recipient.Count -ne 1 -or $recipient[0].Trustee -ne 'auditor@contoso.test'){throw 'Recipient permission evidence mismatch'}
 @{passed=$true;client='ExchangeOnlineManagement';version='3.11.0';commands=6;mailbox_permission_rows=$permissions.Count;recipient_permission_rows=$recipient.Count;transport_rule_rows=$rules.Count;accepted_domain_rows=$domains.Count} | ConvertTo-Json | Set-Content /state/exchange-collection-report.json
} finally {Disconnect-ExchangeOnline -Confirm:$false | Out-Null}

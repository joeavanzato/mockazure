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
 if([TimeZoneInfo]::Local.GetUtcOffset([datetime]::UtcNow).TotalHours -ne -5){throw 'Non-UTC timezone fixture is not active'}
 $expected=Get-Content /state/audit-expected.json -Raw | ConvertFrom-Json
 $start=[datetime]::SpecifyKind(([datetimeoffset]$expected.start).UtcDateTime,[DateTimeKind]::Unspecified)
 $end=[datetime]::SpecifyKind(([datetimeoffset]$expected.end).UtcDateTime,[DateTimeKind]::Unspecified)
 $all=[System.Collections.Generic.List[object]]::new()
 $session=[guid]::NewGuid().ToString()
 $sizes=@()
 for($i=0;$i -lt 4;$i++) {
   $page=@(Search-UnifiedAuditLog -StartDate $start -EndDate $end -SessionId $session -SessionCommand ReturnLargeSet -ResultSize 500 -Operations Set-Mailbox -FreeText workflow)
   $sizes+= $page.Count
   foreach($row in $page){$all.Add($row)}
 }
 if(($sizes -join ',') -ne '500,500,101,0'){throw "Audit paging mismatch: $sizes"}
 $ids=@($all | ForEach-Object {$_.Identity})
 if(@($ids | Select-Object -Unique).Count -ne 1101){throw 'Duplicate or missing audit IDs'}
 if(@(Compare-Object $expected.ids $ids).Count -ne 0){throw 'Audit IDs differ from canonical evidence'}
 for($i=0;$i -lt $all.Count;$i++) {
   $row=$all[$i]
   if($row.ResultIndex -ne ($i+1) -or $row.ResultCount -ne 1101){throw 'Audit session counts differ'}
   $audit=$row.AuditData | ConvertFrom-Json
   if($audit.Id -ne $row.Identity -or $audit.OrganizationId -ne $config.tenant_id){throw 'AuditData identity mismatch'}
 }
 $formatted=@(Search-UnifiedAuditLog -StartDate $start -EndDate $end -ResultSize 1 -Formatted -Operations Set-Mailbox -FreeText workflow)
 if($formatted[0].RecordType -ne 'ExchangeAdmin'){throw 'Formatted record type mismatch'}
 @{passed=$true;client='ExchangeOnlineManagement';version='3.11.0';records=$all.Count;page_sizes=$sizes;timezone=[TimeZoneInfo]::Local.Id;utc_unspecified_dates=$true} | ConvertTo-Json -Depth 5 | Set-Content /state/exchange-workflow-report.json
} finally {Disconnect-ExchangeOnline -Confirm:$false | Out-Null}

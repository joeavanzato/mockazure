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
 Connect-ExchangeOnline -AccessToken $token.access_token -ConnectionUri $config.endpoints.exchange -AzureADAuthorizationEndpointUri "$($config.endpoints.login)/organizations" -AppId $config.client_id -Organization $config.tenant_domain -ShowBanner:$false -SkipLoadingFormatData -SkipLoadingCmdletHelp -DisableWAM -EnableErrorReporting:$false -LogDirectoryPath /state/exo-logs -PageSize 1
} catch {
 $cause=$_.Exception
 while($cause){Write-Output ($cause.GetType().Name+': '+$cause.Message);$cause=$cause.InnerException}
 throw
}
try {
 if([TimeZoneInfo]::Local.GetUtcOffset([datetime]::UtcNow).TotalHours -ne -5){throw 'Non-UTC timezone fixture is not active'}
 $delegate=Get-Content /state/delegate-expected.json -Raw | ConvertFrom-Json
 $classic=@(Get-Recipient -Identity $config.mailbox_upn)
 if($classic.Count -ne 1 -or $classic[0].ExternalDirectoryObjectId -ne $config.user_id){throw 'Classic recipient identity mismatch'}
 foreach($selector in @('Identity','UserPrincipalName','PrimarySmtpAddress','ExternalDirectoryObjectId')) {
   $parameters=@{$selector=$(if($selector -eq 'ExternalDirectoryObjectId'){$config.user_id}else{$config.mailbox_upn})}
   $actual=@(Get-EXORecipient @parameters -ErrorAction Stop)
   if($actual.Count -ne 1 -or $actual[0].ExternalDirectoryObjectId -ne $config.user_id){throw "Compiled recipient $selector mismatch"}
 }
 $listed=@(Get-EXORecipient -ResultSize Unlimited)
 if($listed.Count -ne 2 -or @($listed | Select-Object -ExpandProperty ExternalDirectoryObjectId -Unique).Count -ne 2){throw 'Compiled recipient paging mismatch'}
 $filtered=@(Get-EXORecipient -Filter "DisplayName -eq 'Collection owner'")
 if($filtered.Count -ne 1 -or $filtered[0].ExternalDirectoryObjectId -ne $config.user_id){throw 'Recipient filter mismatch'}
 Add-MailboxPermission -Identity $config.mailbox_upn -User $delegate.upn -AccessRights FullAccess -AutoMapping:$false -Confirm:$false | Out-Null
 $full=@(Get-EXOMailboxPermission -Identity $config.mailbox_upn)
 if(@($full | Where-Object { $_.User -eq $delegate.upn -and $_.AccessRights -contains 'FullAccess' }).Count -ne 1){throw 'Compiled FullAccess mismatch'}
 Add-RecipientPermission -Identity $config.mailbox_upn -Trustee $delegate.upn -AccessRights SendAs -Confirm:$false | Out-Null
 $send=@(Get-EXORecipientPermission -Identity $config.mailbox_upn)
 if(@($send | Where-Object { $_.Trustee -eq $delegate.upn -and $_.AccessRights -contains 'SendAs' }).Count -ne 1){throw 'Compiled SendAs mismatch'}
 Set-Mailbox -Identity $config.mailbox_upn -GrantSendOnBehalfTo @($delegate.upn) -Confirm:$false
 $box=Get-Mailbox -Identity $config.mailbox_upn
 if($box.GrantSendOnBehalfTo -notcontains $delegate.id){throw 'SendOnBehalf target mismatch'}
 Set-Mailbox -Identity $config.mailbox_upn -GrantSendOnBehalfTo @{Remove=$delegate.upn} -Confirm:$false
 Remove-MailboxPermission -Identity $config.mailbox_upn -User $delegate.upn -AccessRights FullAccess -Confirm:$false
 Remove-RecipientPermission -Identity $config.mailbox_upn -Trustee $delegate.upn -AccessRights SendAs -Confirm:$false
 if(@(Get-EXOMailboxPermission -Identity $config.mailbox_upn | Where-Object {$_.User -eq $delegate.upn}).Count){throw 'FullAccess removal mismatch'}
 if(@(Get-EXORecipientPermission -Identity $config.mailbox_upn | Where-Object {$_.Trustee -eq $delegate.upn}).Count){throw 'SendAs removal mismatch'}
 if(@((Get-Mailbox -Identity $config.mailbox_upn).GrantSendOnBehalfTo).Count){throw 'SendOnBehalf removal mismatch'}
 $groups=@(Get-RoleGroup)
 if('Recipient Management' -notin $groups.Name){throw 'Built-in role group missing'}
 Add-RoleGroupMember -Identity 'Recipient Management' -Member $delegate.upn -Confirm:$false
 $members=@(Get-RoleGroupMember -Identity 'Recipient Management')
 if($delegate.id -notin $members.ExternalDirectoryObjectId){throw 'Role member add mismatch'}
 Remove-RoleGroupMember -Identity 'Recipient Management' -Member $delegate.upn -Confirm:$false
 if(@(Get-RoleGroupMember -Identity 'Recipient Management').Count){throw 'Role member remove mismatch'}
 $assignment=New-ManagementRoleAssignment -Name 'Native recipient assignment' -Role 'Mail Recipients' -User $delegate.upn -RecipientRelativeWriteScope Organization -Confirm:$false
 $read=Get-ManagementRoleAssignment -Identity $assignment.Guid
 if($read.Role -ne 'Mail Recipients' -or $read.RoleAssignee -ne $delegate.id){throw 'Role assignment read mismatch'}
 Remove-ManagementRoleAssignment -Identity $assignment.Guid -Confirm:$false
 if(@(Get-ManagementRoleAssignment -RoleAssignee $delegate.id).Count){throw 'Role assignment removal mismatch'}
 $configurationCases=@(
   @{Kind='RemoteDomain';Create=@{DomainName='*.example.test'};Update=@{AutoForwardEnabled=$false};Field='AutoForwardEnabled';Expected=$false},
   @{Kind='InboundConnector';Create=@{SenderDomains=@('*.example.test');ConnectorType='Partner'};Update=@{RequireTls=$false};Field='RequireTls';Expected=$false},
   @{Kind='OutboundConnector';Create=@{RecipientDomains=@('example.test');UseMXRecord=$true;ConnectorType='Partner'};Update=@{Enabled=$false};Field='Enabled';Expected=$false},
   @{Kind='AuthenticationPolicy';Create=@{};Update=@{AllowBasicAuthSmtp=$true};Field='AllowBasicAuthSmtp';Expected=$true},
   @{Kind='OrganizationRelationship';Create=@{DomainNames=@('example.test')};Update=@{FreeBusyAccessEnabled=$true};Field='FreeBusyAccessEnabled';Expected=$true},
   @{Kind='SharingPolicy';Create=@{Domains=@('*:CalendarSharingFreeBusySimple')};Update=@{Enabled=$false};Field='Enabled';Expected=$false}
 )
 foreach($case in $configurationCases){
   $create=@{Name='Native '+$case.Kind;Confirm=$false}+$case.Create
   $created=& ('New-'+$case.Kind) @create
   if(-not $created.Guid){throw 'Missing configuration GUID'}
   $update=@{Identity=$created.Guid;Confirm=$false}+$case.Update
   & ('Set-'+$case.Kind) @update
   $read=& ('Get-'+$case.Kind) -Identity $created.Identity
   if($read.($case.Field) -ne $case.Expected -or $read.Guid -ne $created.Guid){throw 'Configuration roundtrip mismatch'}
   & ('Remove-'+$case.Kind) -Identity $created.Identity -Confirm:$false
   $absent=$false
   try { & ('Get-'+$case.Kind) -Identity $created.Identity -ErrorAction Stop | Out-Null } catch { if($_.Exception.Message -match '404|not found'){ $absent=$true } else { throw } }
   if(-not $absent){throw 'Removed configuration remains visible'}
 }
 if(-not (Get-AdminAuditLogConfig).UnifiedAuditLogIngestionEnabled){throw 'Unexpected audit ingestion default'}
 Set-AdminAuditLogConfig -UnifiedAuditLogIngestionEnabled $false -Confirm:$false
 if((Get-AdminAuditLogConfig).UnifiedAuditLogIngestionEnabled){throw 'Audit ingestion disable failed'}
 Set-AdminAuditLogConfig -UnifiedAuditLogIngestionEnabled $true -Confirm:$false
 if(-not (Get-AdminAuditLogConfig).UnifiedAuditLogIngestionEnabled){throw 'Audit ingestion restore failed'}
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
 @{passed=$true;client='ExchangeOnlineManagement';version='3.11.0';records=$all.Count;page_sizes=$sizes;timezone=[TimeZoneInfo]::Local.Id;utc_unspecified_dates=$true;role_management=$true;security_configuration=$true;recipient_delegation=$true;recipient_count=$listed.Count} | ConvertTo-Json -Depth 5 | Set-Content /state/exchange-workflow-report.json
} finally {Disconnect-ExchangeOnline -Confirm:$false | Out-Null}

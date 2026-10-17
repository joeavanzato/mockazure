param([Parameter(Mandatory)][string]$CasePath)
$ErrorActionPreference='Stop'
$case=Get-Content $CasePath -Raw | ConvertFrom-Json -AsHashtable -DateKind String
$config=Get-Content /state/client.json -Raw | ConvertFrom-Json
. /project/integrations/powershell/Connect-MockAzure.ps1
if($case.mode -eq 'application'){Connect-MockAzure -ConfigPath /state/client.json}
else {Connect-MockAzure -ConfigPath /state/client.json -Mode AuthorizationCode -TokenFile /state/delegated-token.json}
foreach($name in @('Microsoft.Graph.Users','Microsoft.Graph.Mail')){Import-Module $name -RequiredVersion 2.35.1}
$hashes=@{}
$telemetry=[Collections.Generic.List[string]]::new()
$root=if($case.collector -eq 'hawk'){
 '/vendor/collectors/hawk/hawk-a44536f2b4225dd8ea2e463db88d5db40cfac8c7/Hawk'
}else{
 '/vendor/collectors/Microsoft-Extractor-Suite/Microsoft-Extractor-Suite-47565f7842784cd3667ff630e3dccb49fa3408ed'
}
New-Item -ItemType Directory -Path $case.output_dir -Force | Out-Null
$connected=$false
try {
 if($case.function -notin @('Get-MailboxRulesGraph','Get-UALGraph')){
  [Reflection.Assembly]::LoadFrom('/vendor/dependencies/Microsoft.Extensions.DependencyInjection.Abstractions/8.0.0/lib/net8.0/Microsoft.Extensions.DependencyInjection.Abstractions.dll') | Out-Null
  Import-Module ExchangeOnlineManagement -RequiredVersion 3.11.0
  if($case.mode -eq 'application'){
   $secret=(Get-Content $config.secret_file -Raw | ConvertFrom-Json).secret
   $token=Invoke-RestMethod -Method POST -Uri "$($config.endpoints.login)/$($config.tenant_id)/oauth2/v2.0/token" -Body @{
    grant_type='client_credentials';client_id=$config.client_id;client_secret=$secret;scope='https://outlook.office365.com/.default'
   }
  }else{$token=Get-Content /state/exchange-delegated-token.json -Raw | ConvertFrom-Json}
  $connect=@{AccessToken=$token.access_token;ConnectionUri=$config.endpoints.exchange
   AzureADAuthorizationEndpointUri="$($config.endpoints.login)/organizations"
   ShowBanner=$false;SkipLoadingFormatData=$true;SkipLoadingCmdletHelp=$true
   DisableWAM=$true;EnableErrorReporting=$false;LogDirectoryPath='/state/exo-logs'}
  if($case.mode -eq 'application'){$connect.AppId=$config.client_id;$connect.Organization=$config.tenant_domain}
  else{$connect.UserPrincipalName=$config.mailbox_upn}
  Connect-ExchangeOnline @connect
  $connected=$true
 }
 $hiddenCheck=$false
 if($case.function -eq 'Get-HawkUserInboxRule' -and -not $case.denied -and $case.hidden_id){
  $all=@(Get-InboxRule -Mailbox $config.mailbox_upn -IncludeHidden)
  $visible=@(Get-InboxRule -Mailbox $config.mailbox_upn)
  if(@($all | Where-Object {$_.RuleIdentity -eq $case.hidden_id -and $_.IsHidden}).Count -ne 1 -or
     @($visible | Where-Object {$_.RuleIdentity -eq $case.hidden_id}).Count){throw 'Hidden rule visibility mismatch'}
  $hiddenCheck=$true
 }
 if($case.denied){
  $admin=@{Authorization='Bearer '+(Get-Content /state/collector-admin.token -Raw).Trim()}
  Invoke-RestMethod -Method PUT -Uri "$($config.endpoints.graph)/_mock/tenants/$($config.tenant_id)/mail/settings" -Headers $admin -ContentType 'application/json' -Body '{"exchange_enabled":false,"audit_tier":"none"}' | Out-Null
 }
 if($case.collector -eq 'hawk'){
  Import-Module PSFramework -RequiredVersion 1.12.346
  $paths=@('internal/functions/Write-HawkBanner.ps1','internal/functions/Write-HawkConfigurationComplete.ps1',
   'internal/functions/Test-UserObject.ps1','internal/functions/Test-HawkGlobalObject.ps1',
   'internal/functions/Test-EXOConnection.ps1','internal/functions/Test-GraphConnection.ps1','internal/functions/Initialize-HawkGlobalObject.ps1',
   'internal/functions/Out-LogFile.ps1','internal/functions/Out-MultipleFileType.ps1',
   "functions/User/$($case.function).ps1")
  $module=New-Module -Name OriginalHawkMail -ArgumentList $root,$paths,$telemetry,$case.function -ScriptBlock {
   param($root,$paths,$telemetry,$function)
   $script:telemetry=$telemetry
   foreach($path in $paths){. "$root/$path"}
   function New-AIClient {param($key) $script:telemetry.Add('New-AIClient');[pscustomobject]@{disabled=$true}}
   function Send-AIEvent {param($Event) $script:telemetry.Add('Send-AIEvent')}
   function Connect-ExchangeOnline {throw 'Original Hawk attempted an unconfigured reconnect'}
   function Connect-MgGraph {throw 'Original Hawk attempted an unconfigured reconnect'}
   Export-ModuleMember -Function Initialize-HawkGlobalObject,$function
  }
  foreach($path in $paths){$hashes["$root/$path"]=(Get-FileHash "$root/$path" -Algorithm SHA256).Hash.ToLowerInvariant()}
  Import-Module $module
  Initialize-HawkGlobalObject -StartDate $case.start -EndDate $case.end -FilePath $case.output_dir -SkipUpdate -NonInteractive -Force
  & $case.function -UserPrincipalName $config.mailbox_upn
 }else{
  $tokens=$null;$errors=$null
  $ast=[Management.Automation.Language.Parser]::ParseFile("$root/Microsoft-Extractor-Suite.psm1",[ref]$tokens,[ref]$errors)
  if($errors.Count){throw 'Original MES module parse failed'}
  $declarations=$ast.EndBlock.Statements | Where-Object {
   $_ -is [Management.Automation.Language.FunctionDefinitionAst] -or $_ -is [Management.Automation.Language.TypeDefinitionAst] -or
   $_.Extent.Text -ceq '$script:LogLevel = [LogLevel]::Standard'
  }
  $source=($declarations | ForEach-Object {$_.Extent.Text}) -join [Environment]::NewLine
  $scriptPath=switch($case.function){
   'Get-MailboxRules' {'Scripts/Get-Rules.ps1'}
   'Get-MailboxRulesGraph' {'Scripts/Get-Rules.ps1'}
   'Get-MessageTraceLog' {'Scripts/Get-MessageTraceLog.ps1'}
   'Get-UALGraph' {'Scripts/Get-UALGraph.ps1'}
   default {throw 'Unqualified collector'}
  }
  $module=New-Module -Name OriginalMESMail -ArgumentList $source,$root,$config.endpoints.graph,$case.output_dir,$scriptPath,$case.function -ScriptBlock {
   param($source,$root,$origin,$output,$scriptPath,$function)
   . ([scriptblock]::Create($source))
   . "$root/$scriptPath"
   . /project/integrations/powershell/Graph-LocalTransport.ps1 -LocalOrigin $origin
   $script:logFile=Join-Path $output 'collector.log'
   function Connect-ExchangeOnline {throw 'Original MES attempted an unconfigured reconnect'}
   Export-ModuleMember -Function $function
  }
  foreach($path in @('Microsoft-Extractor-Suite.psm1',$scriptPath)){
   $hashes["$root/$path"]=(Get-FileHash "$root/$path" -Algorithm SHA256).Hash.ToLowerInvariant()
  }
  Import-Module $module
  $parameters=@{OutputDir=$case.output_dir;LogLevel='Standard'}
  switch($case.function){
   'Get-UALGraph' {
    $parameters.StartDate=$case.audit_start;$parameters.EndDate=$case.audit_end
    $parameters.Operations=$case.audit_operations;$parameters.SearchName='MockAzure collector qualification'
    $parameters.UseV1=$true;$parameters.Output=$case.audit_output
   }
   'Get-MessageTraceLog' {
    $parameters.UserIds=@($config.mailbox_upn)
    $parameters.StartDate=$config.mail_fixture.trace_start;$parameters.EndDate=$config.mail_fixture.trace_end
   }
   default {$parameters.UserIds=$config.mailbox_upn}
  }
  & $case.function @parameters
 }
 $errorText=$null
}catch{$errorText=$_.Exception.Message}
finally{
 if($case.denied -and $admin){
  Invoke-RestMethod -Method PUT -Uri "$($config.endpoints.graph)/_mock/tenants/$($config.tenant_id)/mail/settings" -Headers $admin -ContentType 'application/json' -Body '{"exchange_enabled":true,"audit_tier":"standard"}' | Out-Null
 }
 $report=@{mode=$case.mode;collector=$case.collector;function=$case.function
  source_hashes=$hashes;hidden_check=$hiddenCheck;telemetry_calls=@($telemetry.ToArray());error=$errorText
  files=@(Get-ChildItem $case.output_dir -Recurse -File | ForEach-Object {$_.FullName})}
 $report | ConvertTo-Json -Depth 12 | Set-Content $case.report
 if($connected){Disconnect-ExchangeOnline -Confirm:$false | Out-Null}
 Disconnect-MgGraph | Out-Null
 if($module){Remove-Module $module -ErrorAction SilentlyContinue}
}
if($errorText){throw $errorText}

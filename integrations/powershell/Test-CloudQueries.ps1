param([Parameter(Mandatory)][string]$Profile,[Parameter(Mandatory)][string]$Workflow,[Parameter(Mandatory)][string]$Report)
$ErrorActionPreference='Stop'
$config=Get-Content $Profile -Raw | ConvertFrom-Json -AsHashtable
foreach($address in @($config.endpoint,$config.logs_endpoint)){
 $origin=[uri]$address
 if($origin.Scheme -ne 'https' -or $origin.Host -ne 'localhost'){throw 'Explicit local HTTPS required'}
}
Import-Module Az.Accounts -RequiredVersion 5.5.3
Import-Module Az.ResourceGraph -RequiredVersion 1.3.0
Import-Module Az.OperationalInsights -RequiredVersion 3.4.1
Import-Module Az.Monitor -RequiredVersion 8.1.0
Disable-AzContextAutosave -Scope Process | Out-Null
Update-AzConfig -EnableDataCollection $false -CheckForUpgrade $false -Scope Process | Out-Null
Add-AzEnvironment -Name MockAzureQueries -ActiveDirectoryAuthority $config.endpoint -ResourceManagerUrl $config.endpoint -ActiveDirectoryServiceEndpointResourceId 'https://management.azure.com' -AzureOperationalInsightsEndpoint ($config.logs_endpoint + '/v1') -AzureOperationalInsightsEndpointResourceId 'https://api.loganalytics.io' -MicrosoftGraphUrl $config.endpoint -MicrosoftGraphEndpointResourceId 'https://graph.microsoft.com/' -GraphUrl $config.endpoint -GraphEndpointResourceId 'https://graph.microsoft.com/' | Out-Null
$token=(Get-Content $config.token_file -Raw | ConvertFrom-Json).access_token
Connect-AzAccount -AccessToken $token -AccountId $config.client_id -Tenant $config.tenant_id -Environment MockAzureQueries -Scope Process -SkipContextPopulation | Out-Null
Set-AzContext -Subscription $config.subscription_id -Tenant $config.tenant_id -Scope Process | Out-Null
$seen=@();$skip=$null
 do {
  $parameters=@{Query=$config.inventory_query;Subscription=$config.subscription_id;First=1}
  if($skip){$parameters.SkipToken=$skip}
  $page=Search-AzGraph @parameters
  $seen+=@($page.Data | ForEach-Object {$_.id.ToLowerInvariant()})
  $skip=$page.SkipToken
 }while($skip)
if(($seen|Sort-Object|ConvertTo-Json -Compress) -ne ($config.expected_resources|Sort-Object|ConvertTo-Json -Compress)){throw 'Inventory paging differs'}
$checks=@{inventory_paging=$true}
if($Workflow -eq 'write'){
 $log=New-AzDiagnosticSettingLogSettingsObject -Enabled $true -Category 'StorageWrite'
 New-AzDiagnosticSetting -ResourceId ($config.source.TrimStart('/')) -Name $config.setting -WorkspaceId $config.workspace_resource -StorageAccountId $config.account -LogAnalyticsDestinationType 'Dedicated' -Log @($log) | Out-Null
}
$got=Get-AzDiagnosticSetting -ResourceId ($config.source.TrimStart('/')) -Name $config.setting
if($got.WorkspaceId -ine $config.workspace_resource){throw 'Diagnostic setting differs'}
$checks.diagnostic_settings=$true
if($Workflow -eq 'read'){
 $logToken=(Get-Content $config.logs_token_file -Raw | ConvertFrom-Json).access_token
 Connect-AzAccount -AccessToken $logToken -AccountId $config.client_id -Tenant $config.tenant_id -Environment MockAzureQueries -Scope Process -SkipContextPopulation | Out-Null
 try {
  $result=Invoke-AzOperationalInsightsQuery -WorkspaceId $config.workspace_id -Query $config.logs_query -Timespan ([TimeSpan]::FromDays(30))
 } catch { throw }
 if(@($result.Results).Count -ne 1 -or $result.Results[0].n -ne $config.expected_logs){throw 'Logs query differs'}
 $checks.native_logs_query=$true;$checks.cross_language_reads=$true
}
$versions=@{}
foreach($name in @('Az.Accounts','Az.ResourceGraph','Az.OperationalInsights','Az.Monitor')){$versions[$name]=(Get-Module $name).Version.ToString()}
@{ecosystem='powershell';passed=$true;assertions=$checks;clientVersions=$versions;transport='module';workflow=$Workflow}|ConvertTo-Json -Depth 20|Set-Content $Report

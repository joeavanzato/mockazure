param([Parameter(Mandatory)][string]$Profile,[Parameter(Mandatory)][string]$Workflow,[Parameter(Mandatory)][string]$Report)
$ErrorActionPreference='Stop'
$config=Get-Content $Profile -Raw | ConvertFrom-Json -AsHashtable
$origin=[uri]$config.endpoint
if($origin.Scheme -ne 'https' -or $origin.Host -ne 'localhost'){throw 'Explicit local HTTPS origin required'}
Import-Module Az.Accounts -RequiredVersion 4.0.2
Import-Module Az.Resources -RequiredVersion 7.8.0
Disable-AzContextAutosave -Scope Process | Out-Null
Update-AzConfig -EnableDataCollection $false -CheckForUpgrade $false -Scope Process | Out-Null
Add-AzEnvironment -Name MockAzureCloud -ActiveDirectoryAuthority $config.endpoint -ResourceManagerUrl $config.endpoint -ActiveDirectoryServiceEndpointResourceId 'https://management.azure.com' -MicrosoftGraphUrl $config.endpoint -MicrosoftGraphEndpointResourceId 'https://graph.microsoft.com/' -GraphUrl $config.endpoint -GraphEndpointResourceId 'https://graph.microsoft.com/' | Out-Null
$token=(Get-Content $config.token_file -Raw | ConvertFrom-Json).access_token
Connect-AzAccount -AccessToken $token -AccountId $config.client_id -Tenant $config.tenant_id -Environment MockAzureCloud -Scope Process -SkipContextPopulation | Out-Null
Set-AzContext -Subscription $config.subscription_id -Tenant $config.tenant_id -Scope Process | Out-Null
$started=[DateTimeOffset]::UtcNow
if($Workflow -eq 'cleanup'){
  $rows=@($config.resources);[array]::Reverse($rows)
  foreach($row in $rows){Remove-AzResource -ResourceId $row.id -ApiVersion $row.version -Force | Out-Null}
  $checks=@{deleted=$rows.Count}
}else{
  foreach($row in $config.resources){
    $parameters=@{ResourceId=$row.id;ApiVersion=$row.version;Properties=[pscustomobject]$row.body;IsFullObject=$true;Force=$true}
    New-AzResource @parameters | Out-Null
    # Get-AzResource supplies its own separator after the environment origin.
    $got=Get-AzResource -ResourceId ($row.id.TrimStart('/')) -ApiVersion $row.version -ExpandProperties
    if($got.ResourceId -ine $row.id){throw 'Resource identity differs'}
  }
  foreach($row in $config.read_resources){
    # Get-AzResource supplies its own separator after the environment origin.
    $got=Get-AzResource -ResourceId ($row.id.TrimStart('/')) -ApiVersion $row.version -ExpandProperties
    if($got.ResourceId -ine $row.id){throw 'Cross-language identity differs'}
  }
  $target=$config.resources | Where-Object type -eq 'Microsoft.Compute/virtualMachines'
  Set-AzResource -ResourceId $target.id -ApiVersion $target.version -Tag @{writer='powershell'} -Force | Out-Null
  if((Get-AzResource -ResourceId ($target.id.TrimStart('/')) -ApiVersion $target.version).Tags.writer -ne 'powershell'){throw 'Update differs'}
  $checks=@{created=$config.resources.Count;cross_reads=$config.read_resources.Count;updated=$true}
}
@{ecosystem='powershell';clientVersions=@{'Az.Accounts'=(Get-Module Az.Accounts).Version.ToString();'Az.Resources'=(Get-Module Az.Resources).Version.ToString()};transport='module';workflow=$Workflow;assertions=$checks;passed=$true;elapsedSeconds=([DateTimeOffset]::UtcNow-$started).TotalSeconds}|ConvertTo-Json -Depth 30 | Set-Content $Report

param([Parameter(Mandatory)][string]$Profile,[Parameter(Mandatory)][string]$Report)
$ErrorActionPreference='Stop'
$p=Get-Content $Profile -Raw | ConvertFrom-Json -AsHashtable
foreach($endpoint in @($p.blob_endpoint,$p.file_endpoint)){$uri=[uri]$endpoint;if($uri.Scheme -ne 'https' -or $uri.Host -notin @('localhost','127.0.0.1')){throw 'Explicit local HTTPS required'}}
Import-Module Az.Accounts -RequiredVersion 4.0.2
Import-Module Az.Storage -RequiredVersion 8.1.0
Disable-AzContextAutosave -Scope Process | Out-Null
Update-AzConfig -EnableDataCollection $false -CheckForUpgrade $false -Scope Process | Out-Null
$connection="DefaultEndpointsProtocol=https;AccountName=$($p.account);AccountKey=$($p.key);BlobEndpoint=$($p.blob_endpoint);FileEndpoint=$($p.file_endpoint);QueueEndpoint=$($p.queue_endpoint);TableEndpoint=$($p.table_endpoint)"
$context=New-AzStorageContext -ConnectionString $connection
New-AzStorageContainer -Name 'powershell-evidence' -Context $context -Permission Off | Out-Null
$containers=@(Get-AzStorageContainer -Context $context)
if('powershell-evidence' -notin $containers.Name){throw 'Container missing'}
New-AzStorageShare -Name 'powershell-evidence' -Context $context | Out-Null
$shares=@(Get-AzStorageShare -Context $context)
if('powershell-evidence' -notin $shares.Name){throw 'Share missing'}
@{passed=$true;created=2;clientVersions=@{'Az.Accounts'=(Get-Module Az.Accounts).Version.ToString();'Az.Storage'=(Get-Module Az.Storage).Version.ToString()}}|ConvertTo-Json -Depth 10|Set-Content $Report

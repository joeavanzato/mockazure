param([Parameter(Mandatory)][string]$Profile,[Parameter(Mandatory)][string]$Report,[Parameter(Mandatory)][string]$Backup,[ValidateSet('write','read','restore')][string]$Mode='write')
$ErrorActionPreference='Stop'
$p=Get-Content $Profile -Raw | ConvertFrom-Json -AsHashtable
$origins=@($p.table_endpoint,$p.queue_endpoint,$p.vault_endpoint,$p.arm_endpoint) | ForEach-Object {
  $uri=[uri]$_
  if($uri.Scheme -ne 'https' -or ($uri.Host -notin @('localhost','127.0.0.1') -and $uri.Host -ne $p.vault_host)){throw 'Explicit local HTTPS required'}
  $uri.GetLeftPart([System.UriPartial]::Authority)
}
Import-Module Az.Accounts -RequiredVersion 5.5.3
Import-Module Az.Storage -RequiredVersion 8.1.0
Import-Module Az.KeyVault -RequiredVersion 6.6.1
Disable-AzContextAutosave -Scope Process | Out-Null
Update-AzConfig -EnableDataCollection $false -CheckForUpgrade $false -Scope Process | Out-Null
Add-AzEnvironment -Name MockAzureStructured -ActiveDirectoryAuthority $p.arm_endpoint -ResourceManagerUrl $p.arm_endpoint -ActiveDirectoryServiceEndpointResourceId 'https://management.azure.com' -MicrosoftGraphUrl $p.arm_endpoint -MicrosoftGraphEndpointResourceId 'https://graph.microsoft.com/' -GraphUrl $p.arm_endpoint -GraphEndpointResourceId 'https://graph.microsoft.com/' -AzureKeyVaultDnsSuffix $p.vault_suffix -AzureKeyVaultServiceEndpointResourceId 'https://vault.azure.net' | Out-Null
Connect-AzAccount -AccessToken $p.arm_token -KeyVaultAccessToken $p.token -AccountId $p.client_id -Tenant $p.tenant_id -Environment MockAzureStructured -Scope Process -SkipContextPopulation | Out-Null
$connection="DefaultEndpointsProtocol=https;AccountName=$($p.account);AccountKey=$($p.key);QueueEndpoint=$($p.queue_endpoint);TableEndpoint=$($p.table_endpoint);BlobEndpoint=$($p.blob_endpoint);FileEndpoint=$($p.file_endpoint)"
$context=New-AzStorageContext -ConnectionString $connection
$table='PowershellPersist'
$queueName='powershell-persist'
$secretName='powershell-secret'
$checks=@{}
function TableRest([string]$Method,[string]$Path,$Body=$null,[string]$ETag=''){
  $uri=[uri]($p.table_endpoint.TrimEnd('/')+'/'+$Path)
  if($uri.GetLeftPart([System.UriPartial]::Authority) -notin $origins){throw 'Nonlocal table request rejected'}
  $head=@{Authorization='Bearer '+$p.storage_token;'x-ms-version'='2019-02-02';Accept='application/json;odata=fullmetadata'}
  if($ETag){$head['If-Match']=$ETag}
  $args=@{Uri=$uri;Method=$Method;Headers=$head;SkipHttpErrorCheck=$true;MaximumRedirection=0}
  if($null -ne $Body){$args.Body=[Text.Encoding]::UTF8.GetBytes(($Body|ConvertTo-Json -Depth 20 -Compress));$args.ContentType='application/json'}
  Invoke-WebRequest @args
}
if($Mode -eq 'write'){
  New-AzStorageTable -Name $table -Context $context | Out-Null
  $result=TableRest 'POST' $table @{PartitionKey='p';RowKey='one';Count='9007199254740993';'Count@odata.type'='Edm.Int64';Bytes='AAH/';'Bytes@odata.type'='Edm.Binary'}
  if($result.StatusCode -ne 201){throw "Table create returned $($result.StatusCode)"}
  $etag=$result.Headers.ETag[0]
  $target=$table+"(PartitionKey='p',RowKey='one')"
  if((TableRest 'PATCH' $target @{Keep=$true} $etag).StatusCode -ne 204){throw 'Table merge failed'}
  if((TableRest 'PATCH' $target @{Keep=$false} $etag).StatusCode -ne 412){throw 'Stale Table ETag accepted'}
  $checks.tableMutation=$true
  $queue=(New-AzStorageQueue -Name $queueName -Context $context).QueueClient
  $none=[Threading.CancellationToken]::None
  $queue.SendMessage('first',[Nullable[TimeSpan]]$null,[Nullable[TimeSpan]]([TimeSpan]::FromSeconds(-1)),$none)|Out-Null
  $message=$queue.ReceiveMessage([Nullable[TimeSpan]]([TimeSpan]::FromSeconds(30)),$none).Value
  $update=$queue.UpdateMessage($message.MessageId,$message.PopReceipt,'second',[TimeSpan]::Zero,$none).Value
  if($update.PopReceipt -eq $message.PopReceipt){throw 'Queue receipt was not rotated'}
  try {$queue.DeleteMessage($message.MessageId,$message.PopReceipt,$none)|Out-Null;throw 'Stale receipt accepted'} catch {if($_.Exception.InnerException.Status -ne 400 -and $_.Exception.Status -ne 400){throw}}
  $again=$queue.ReceiveMessage([Nullable[TimeSpan]]$null,$none).Value
  if($again.MessageText -ne 'second' -or $again.DequeueCount -ne 2){throw 'Queue redelivery differs'}
  $queue.DeleteMessage($again.MessageId,$again.PopReceipt,$none)|Out-Null
  $queue.SendMessage('persistent ☃',[Nullable[TimeSpan]]$null,[Nullable[TimeSpan]]([TimeSpan]::FromSeconds(-1)),$none)|Out-Null
  $checks.queueReceipts=$true
  $a=Set-AzKeyVaultSecret -VaultName $p.vault_name -Name $secretName -SecretValue (ConvertTo-SecureString 'first' -AsPlainText -Force)
  $b=Set-AzKeyVaultSecret -VaultName $p.vault_name -Name $secretName -SecretValue (ConvertTo-SecureString 'second' -AsPlainText -Force)
  if($a.Version -eq $b.Version){throw 'Secret version did not change'}
  if((Get-AzKeyVaultSecret -VaultName $p.vault_name -Name $secretName -Version $a.Version -AsPlainText) -ne 'first'){throw 'Version lookup differs'}
  Backup-AzKeyVaultSecret -VaultName $p.vault_name -Name $secretName -OutputFile $Backup -Force | Out-Null
  Remove-AzKeyVaultSecret -VaultName $p.vault_name -Name $secretName -Force | Out-Null
  if((Get-AzKeyVaultSecret -VaultName $p.vault_name -Name $secretName -InRemovedState).Name -ne $secretName){throw 'Deleted secret missing'}
  Undo-AzKeyVaultSecretRemoval -VaultName $p.vault_name -Name $secretName | Out-Null
  if((Get-AzKeyVaultSecret -VaultName $p.vault_name -Name $secretName).Version -ne $b.Version){throw 'Recovered version differs'}
  $checks.secretRecovery=$true
}
if($Mode -in @('write','restore')){
  Remove-AzKeyVaultSecret -VaultName $p.vault_name -Name $secretName -Force | Out-Null
  Remove-AzKeyVaultSecret -VaultName $p.vault_name -Name $secretName -InRemovedState -Force | Out-Null
  Restore-AzKeyVaultSecret -VaultName $p.vault_name -InputFile $Backup | Out-Null
  $checks.secretBackupRestore=$true
}
$entity=TableRest 'GET' ($table+"(PartitionKey='p',RowKey='one')")
if($entity.StatusCode -ne 200){throw 'Table evidence missing'}
$data=$entity.Content|ConvertFrom-Json
if($data.Count -ne '9007199254740993' -or !$data.Keep -or $data.Bytes -ne 'AAH/'){throw 'Typed entity changed'}
$queue=(Get-AzStorageQueue -Name $queueName -Context $context).QueueClient
if($queue.PeekMessage().Value.MessageText -ne 'persistent ☃'){throw 'Persisted queue message differs'}
if((Get-AzKeyVaultSecret -VaultName $p.vault_name -Name $secretName -AsPlainText) -ne 'second'){throw 'Persisted secret differs'}
if(@(Get-AzKeyVaultSecret -VaultName $p.vault_name -Name $secretName -IncludeVersions).Count -ne 2){throw 'Secret versions differ'}
$checks.persistentRead=$true
@{passed=$true;mode=$Mode;assertions=$checks;transport='module+rest';restFallbacks=@('Table entity CRUD: Az.Storage has no native entity cmdlets');clientVersions=@{'Az.Accounts'=(Get-Module Az.Accounts).Version.ToString();'Az.Storage'=(Get-Module Az.Storage).Version.ToString();'Az.KeyVault'=(Get-Module Az.KeyVault).Version.ToString()}}|ConvertTo-Json -Depth 20|Set-Content $Report -Encoding utf8

param([Parameter(Mandatory)][string]$Profile,[Parameter(Mandatory)][string]$Report,[switch]$ReadOnly)
$ErrorActionPreference='Stop'
$p=Get-Content $Profile -Raw | ConvertFrom-Json -AsHashtable
foreach($endpoint in @($p.blob_endpoint,$p.file_endpoint)){$uri=[uri]$endpoint;if($uri.Scheme -ne 'https' -or $uri.Host -notin @('localhost','127.0.0.1')){throw 'Explicit local HTTPS required'}}
Import-Module Az.Accounts -RequiredVersion 4.0.2
Import-Module Az.Storage -RequiredVersion 8.1.0
Disable-AzContextAutosave -Scope Process | Out-Null
Update-AzConfig -EnableDataCollection $false -CheckForUpgrade $false -Scope Process | Out-Null
$connection="DefaultEndpointsProtocol=https;AccountName=$($p.account);AccountKey=$($p.key);BlobEndpoint=$($p.blob_endpoint);FileEndpoint=$($p.file_endpoint);QueueEndpoint=$($p.queue_endpoint);TableEndpoint=$($p.table_endpoint)"
$context=New-AzStorageContext -ConnectionString $connection
$source=Join-Path (Split-Path $Report) 'powershell-binary-source.bin'
$download=Join-Path (Split-Path $Report) 'powershell-binary-download.bin'
$data=[byte[]]::new(3145745)
for($i=0;$i -lt $data.Length;$i++){$data[$i]=$i%256}
[IO.File]::WriteAllBytes($source,$data)
$expected=(Get-FileHash $source -Algorithm SHA256).Hash
try{
 if(-not $ReadOnly){
  Set-AzStorageBlobContent -File $source -Container 'powershell-evidence' -Blob 'binary.bin' -Context $context -Force | Out-Null
  Set-AzStorageFileContent -Source $source -ShareName 'powershell-evidence' -Path 'binary.bin' -Context $context -Force | Out-Null
 }
 Get-AzStorageBlobContent -Container 'powershell-evidence' -Blob 'binary.bin' -Destination $download -Context $context -Force | Out-Null
 if((Get-FileHash $download -Algorithm SHA256).Hash -ne $expected){throw 'Blob hash differs'}
 Get-AzStorageFileContent -ShareName 'powershell-evidence' -Path 'binary.bin' -Destination $download -Context $context -Force | Out-Null
 if((Get-FileHash $download -Algorithm SHA256).Hash -ne $expected){throw 'File hash differs'}
 @{passed=$true;bytes=$data.Length;sha256=$expected;clientVersions=@{'Az.Accounts'=(Get-Module Az.Accounts).Version.ToString();'Az.Storage'=(Get-Module Az.Storage).Version.ToString()}}|ConvertTo-Json -Depth 10|Set-Content $Report
}finally{
 Remove-Item -LiteralPath $source,$download -ErrorAction SilentlyContinue
}

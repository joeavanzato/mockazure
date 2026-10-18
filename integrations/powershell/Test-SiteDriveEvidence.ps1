param([ValidateSet('prepare','resume')][string]$Phase='prepare')
$ErrorActionPreference='Stop'
. /project/integrations/powershell/Connect-MockAzure.ps1
Connect-MockAzure -ConfigPath /state/client.json
Import-Module Microsoft.Graph.Sites -RequiredVersion 2.35.1
Import-Module Microsoft.Graph.Files -RequiredVersion 2.35.1
$config=Get-Content /state/client.json -Raw | ConvertFrom-Json
. /project/integrations/powershell/Graph-LocalTransport.ps1 -LocalOrigin $config.endpoints.graph -AllowLocalContentRedirect
$fixture=$config.fixtures.powershell
$predicate=[MockAzure.LocalContentRedirect]::ForOrigin($config.endpoints.graph)
foreach($target in @(
    @{url="$($config.endpoints.graph)/content/$('a'*64)";allowed=$true},
    @{url="https://graph.microsoft.com/content/$('a'*64)";allowed=$false},
    @{url="http://localhost:8443/content/$('a'*64)";allowed=$false},
    @{url="$($config.endpoints.graph)/v1.0/users";allowed=$false},
    @{url="$($config.endpoints.graph)/content/$('a'*64)?extra=1";allowed=$false}
)) {
    $response=[Net.Http.HttpResponseMessage]::new([Net.HttpStatusCode]::Found)
    $response.RequestMessage=[Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get,"$($config.endpoints.graph)/v1.0/drives/local/items/one/content")
    $response.Headers.Location=[uri]$target.url
    if($predicate.Invoke($response) -ne $target.allowed){throw 'Local redirect boundary failed'}
    $response.Dispose()
}
function Read-Delta([string]$Link) {
    $rows=[Collections.Generic.List[object]]::new()
    do {
        $page=Invoke-MgGraphRequest -Uri $Link
        foreach($row in $page.value){$rows.Add($row)}
        $Link=$page.'@odata.nextLink'
    } while($Link)
    return @{rows=@($rows);link=$page.'@odata.deltaLink'}
}
if($Phase -eq 'prepare') {
    $site=Get-MgSite -SiteId $fixture.site
    if($site.Id -ne $fixture.site){throw 'Site identity mismatch'}
    $drives=@(Get-MgSiteDrive -SiteId $fixture.site -All)
    if($drives.Count -ne 1 -or $drives[0].Id -ne $fixture.drive){throw 'Drive discovery mismatch'}
    $files=@(Get-MgDriveItemChild -DriveId $fixture.drive -DriveItemId $fixture.root -All -PageSize 25)
    if($files.Count -ne 101){throw 'Library paging mismatch'}
    foreach($item in $files){
        Get-MgDriveItemContent -DriveId $fixture.drive -DriveItemId $item.Id -OutFile /state/powershell-file.bin
        $digest=(Get-FileHash /state/powershell-file.bin -Algorithm SHA256).Hash.ToLowerInvariant()
        if($digest -cne $config.content_sha256){throw 'Downloaded bytes mismatch'}
    }
    $generated=@(Get-MgDriveItemDelta -DriveId $fixture.drive -DriveItemId $fixture.root -All -PageSize 25)
    if($generated.Count -ne 102){throw 'Generated delta count mismatch'}
    $delta=Read-Delta "$($config.endpoints.graph)/v1.0/drives/$($fixture.drive)/root/delta?`$top=25"
    if($delta.rows.Count -ne 102 -or -not $delta.link){throw 'Missing delta state'}
    @{link=$delta.link} | ConvertTo-Json | Set-Content /state/powershell-delta.json
    @{client='Microsoft Graph PowerShell';version='2.35.1';sites=1;drives=1;files=101;verified_downloads=101;page_size=25;content_sha256=$config.content_sha256} | ConvertTo-Json | Set-Content /state/powershell-report.json
} else {
    $state=Get-Content /state/powershell-delta.json -Raw | ConvertFrom-Json
    $delta=Read-Delta $state.link
    $updated=@($delta.rows | Where-Object id -eq 'powershell!file0')[0]
    $removed=@($delta.rows | Where-Object id -eq 'powershell!file1')[0]
    if($updated.name -ne 'Renamed evidence.bin' -or -not $removed.deleted){throw 'Delta values after restart mismatch'}
    $report=Get-Content /state/powershell-report.json -Raw | ConvertFrom-Json -AsHashtable
    $report.delta_after_restart=$true
    $report | ConvertTo-Json | Set-Content /state/powershell-report.json
}
Disconnect-MgGraph | Out-Null

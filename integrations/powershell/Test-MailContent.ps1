param([ValidateSet('prepare','resume')][string]$Phase='prepare')
$ErrorActionPreference='Stop'
. /project/integrations/powershell/Connect-MockAzure.ps1
Connect-MockAzure -ConfigPath /state/client.json
Import-Module Microsoft.Graph.Mail -RequiredVersion 2.35.1
$config=Get-Content /state/client.json -Raw | ConvertFrom-Json
. /project/integrations/powershell/Graph-LocalTransport.ps1 -LocalOrigin $config.endpoints.graph
$user=$config.powershell_user
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
    $folders=@(Get-MgUserMailFolder -UserId $user -All)
    if($folders.Count -ne 2){throw 'Folder count mismatch'}
    $messages=@(Get-MgUserMessage -UserId $user -All -PageSize 25 -Property id,subject)
    if($messages.Count -ne 101){throw 'Message paging mismatch'}
    Get-MgUserMessageContent -UserId $user -MessageId m0 -OutFile /state/powershell-mime.eml
    $mimeHash=(Get-FileHash /state/powershell-mime.eml -Algorithm SHA256).Hash.ToLowerInvariant()
    if($mimeHash -cne $config.mime_sha256){throw 'MIME bytes mismatch'}
    $attachment=Get-MgUserMessageAttachment -UserId $user -MessageId m0 -AttachmentId a1
    $bytes=$attachment.AdditionalProperties.contentBytes
    if(-not $bytes){$bytes=$attachment.ContentBytes}
    if($bytes -is [string]){$bytes=[Convert]::FromBase64String($bytes)}
    $attachmentHash=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([byte[]]$bytes)).ToLowerInvariant()
    if($attachmentHash -cne $config.attachment_sha256){throw 'Attachment bytes mismatch'}
    $generated=@(Get-MgUserMailFolderMessageDelta -UserId $user -MailFolderId inbox -All -PageSize 25)
    if($generated.Count -ne 101){throw 'Generated delta cmdlet paging mismatch'}
    $delta=Read-Delta "$($config.endpoints.graph)/v1.0/users/$user/mailFolders/inbox/messages/delta?`$top=25"
    if($delta.rows.Count -ne 101 -or -not $delta.link){throw 'Missing delta state'}
    @{link=$delta.link} | ConvertTo-Json | Set-Content /state/powershell-delta.json
    Update-MgUserMessage -UserId $user -MessageId m0 -BodyParameter @{isRead=$true}
    @{client='Microsoft Graph PowerShell';version='2.35.1';folders=2;messages=101;page_size=25;mime_sha256=$mimeHash;attachment_sha256=$attachmentHash} | ConvertTo-Json | Set-Content /state/powershell-report.json
} else {
    $state=Get-Content /state/powershell-delta.json -Raw | ConvertFrom-Json
    $delta=Read-Delta $state.link
    if($delta.rows.Count -ne 2){throw 'Delta count after restart mismatch'}
    $updated=@($delta.rows | Where-Object id -eq m0)[0]
    $removed=@($delta.rows | Where-Object id -eq m1)[0]
    if(-not $updated.isRead -or $removed.'@removed'.reason -ne 'deleted'){throw 'Delta values after restart mismatch'}
    $report=Get-Content /state/powershell-report.json -Raw | ConvertFrom-Json -AsHashtable
    $report.delta_after_restart=$true
    $report | ConvertTo-Json | Set-Content /state/powershell-report.json
}
Disconnect-MgGraph | Out-Null

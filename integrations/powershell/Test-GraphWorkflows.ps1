param([string]$Profile,[string]$Report,[ValidateSet('write','read')][string]$Mode)
$ErrorActionPreference='Stop'
$p=Get-Content $Profile -Raw | ConvertFrom-Json -AsHashtable -DateKind String
Import-Module Microsoft.Graph.Authentication -RequiredVersion 2.35.1
Import-Module Microsoft.Graph.Mail -RequiredVersion 2.35.1
Import-Module Microsoft.Graph.Users.Actions -RequiredVersion 2.35.1
Import-Module Microsoft.Graph.Calendar -RequiredVersion 2.35.1
Import-Module Microsoft.Graph.ChangeNotifications -RequiredVersion 2.35.1
Add-MgEnvironment -Name MockAzure -GraphEndpoint $p.endpoint -AzureADEndpoint $p.endpoint | Out-Null
Connect-MgGraph -AccessToken (ConvertTo-SecureString $p.token -AsPlainText -Force) -Environment MockAzure -NoWelcome
. /project/integrations/powershell/Graph-LocalTransport.ps1 -LocalOrigin $p.endpoint
function Check($value){if(-not $value){throw 'Native Graph workflow assertion failed'}}
function GraphRequest($path,$method='GET',$body){
    $params=@{Uri="$($p.endpoint)/v1.0$path";Method=$method;Headers=@{Authorization="Bearer $($p.token)"}}
    if($null -ne $body){$params.Body=($body | ConvertTo-Json -Depth 30);$params.ContentType='application/json'}
    Invoke-MgGraphRequest @params
}
$checks=@{};$objects=@{}
if($Mode -eq 'write'){
    $sub=New-MgSubscription -BodyParameter $p.subscription_body
    $draft=New-MgUserMessage -UserId $p.owner -BodyParameter $p.message_body
    Check ($draft.Id -and $draft.IsDraft)
    Update-MgUserMessage -UserId $p.owner -MessageId $draft.Id -BodyParameter @{subject="$($p.label) persistent"}
    Check ((Get-MgUserMessage -UserId $p.owner -MessageId $draft.Id).Subject -eq "$($p.label) persistent")
    Send-MgUserMail -UserId $p.owner -BodyParameter @{message=$p.send_body;saveToSentItems=$true}
    $event=New-MgUserEvent -UserId $p.owner -BodyParameter $p.event_body
    $instances=@(Get-MgUserEventInstance -UserId $p.owner -EventId $event.Id -StartDateTime $p.window_start -EndDateTime $p.window_end)
    Check ($instances.Count -eq 3 -and $instances[0].Start.DateTime.Contains('T13:00:00'))
    Check ($instances[1].Start.DateTime.Contains('T14:00:00') -and $instances[2].Start.DateTime.Contains('T14:00:00'))
    # This pinned module lacks the primary calendarView/delta command. Its original
    # Invoke-MgGraphRequest transport is an explicitly reported REST fallback.
    $delta=GraphRequest "/users/$($p.owner)/calendarView/delta?startDateTime=$([uri]::EscapeDataString($p.window_start))&endDateTime=$([uri]::EscapeDataString($p.window_end))"
    Check $delta.'@odata.deltaLink'
    Update-MgUserEvent -UserId $p.owner -EventId $event.Id -BodyParameter @{subject="$($p.label) updated"}
    $changed=Invoke-MgGraphRequest -Uri $delta.'@odata.deltaLink' -Headers @{Authorization="Bearer $($p.token)"}
    Check ($changed.value.Count -gt 0)
    $mail=$null;$invite=$null
    for($i=0;$i -lt 80;$i++){
        $mail=Get-MgUserMessage -UserId $p.recipient | Where-Object Subject -eq $p.send_body.subject | Select-Object -First 1
        $invite=Get-MgUserEvent -UserId $p.recipient | Where-Object ICalUId -eq $event.ICalUId | Select-Object -First 1
        if($mail -and $invite){break};Start-Sleep -Milliseconds 250
    }
    Check ($mail -and $invite -and $invite.Id -ne $event.Id)
    $attachment=Get-MgUserMessageAttachment -UserId $p.recipient -MessageId $mail.Id | Where-Object Name -eq 'evidence.bin' | Select-Object -First 1
    Check ($null -ne $attachment)
    Invoke-MgAcceptUserEvent -UserId $p.recipient -EventId $invite.Id -BodyParameter @{sendResponse=$true}
    Update-MgSubscription -SubscriptionId $sub.Id -BodyParameter @{expirationDateTime=[datetimeoffset]::UtcNow.AddHours(3).ToString('o')}
    $objects=@{message=$draft.Id;event=$event.Id;subscription=$sub.Id;owner=$p.owner}
    foreach($name in @('mail_roundtrip','calendar_recurrence_delta','meeting_response','subscription_management')){$checks[$name]=$true}
}else{
    $fixtures=Get-Content $p.fixtures -Raw | ConvertFrom-Json -AsHashtable -DateKind String
    foreach($f in $fixtures.Values){
        Check ((Get-MgUserMessage -UserId $f.owner -MessageId $f.message).Id -eq $f.message)
        Check ((Get-MgUserEvent -UserId $f.owner -EventId $f.event).Attendees[0].Status.Response -eq 'accepted')
        Update-MgUserEvent -UserId $f.owner -EventId $f.event -BodyParameter @{subject='Cross powershell'}
        Check ((Get-MgUserEvent -UserId $f.owner -EventId $f.event).Subject -eq 'Cross powershell')
    }
    $id=$fixtures.powershell.subscription
    Check ((Get-MgSubscription -SubscriptionId $id).Id -eq $id)
    Invoke-MgReauthorizeSubscription -SubscriptionId $id
    Remove-MgSubscription -SubscriptionId $id
    $checks=@{cross_language_reads=($fixtures.Count -eq 4);subscription_management=$true;persistent_read=$true}
}
$versions=@{}
foreach($module in @('Microsoft.Graph.Authentication','Microsoft.Graph.Mail','Microsoft.Graph.Calendar','Microsoft.Graph.ChangeNotifications','Microsoft.Graph.Users.Actions')){$versions[$module]=(Get-Module $module).Version.ToString()}
@{passed=$true;clientVersions=$versions;assertions=$checks;objects=$objects;restFallbacks=@('primary calendarView/delta through original Invoke-MgGraphRequest')} | ConvertTo-Json -Depth 30 | Set-Content -Path $Report -Encoding utf8

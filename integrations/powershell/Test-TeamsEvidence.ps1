param([ValidateSet('prepare','resume')][string]$Phase='prepare')
$ErrorActionPreference='Stop'
. /project/integrations/powershell/Connect-MockAzure.ps1
Connect-MockAzure -ConfigPath /state/client.json
Import-Module Microsoft.Graph.Teams -RequiredVersion 2.35.1
$config=Get-Content /state/client.json -Raw | ConvertFrom-Json
. /project/integrations/powershell/Graph-LocalTransport.ps1 -LocalOrigin $config.endpoints.graph
$fixture=$config.fixtures.powershell
$chatPath="$($config.endpoints.graph)/v1.0/chats/$([uri]::EscapeDataString($fixture.chat))/messages"
if($Phase -eq 'prepare') {
    $team=Get-MgTeam -TeamId $fixture.team
    if($team.Id -ne $fixture.team){throw 'Team identity mismatch'}
    $joined=@(Get-MgUserJoinedTeam -UserId $config.user_id -All)
    if($fixture.team -notin $joined.Id){throw 'Joined team discovery mismatch'}
    $channels=@(Get-MgTeamChannel -TeamId $fixture.team -All)
    if($fixture.channel -notin $channels.Id){throw 'Channel discovery mismatch'}
    $chats=@(Get-MgUserChat -UserId $config.user_id -All)
    if($fixture.chat -notin $chats.Id){throw 'Chat discovery mismatch'}
    $channelMessages=@(Get-MgTeamChannelMessage -TeamId $fixture.team -ChannelId $fixture.channel -All -PageSize 25)
    $chatMessages=@(Get-MgChatMessage -ChatId $fixture.chat -All -PageSize 25)
    $replies=@(Get-MgTeamChannelMessageReply -TeamId $fixture.team -ChannelId $fixture.channel -ChatMessageId '0' -All -PageSize 25)
    if($channelMessages.Count -ne 101 -or $chatMessages.Count -ne 101 -or $replies.Count -ne 201){throw 'Message or reply pagination mismatch'}
    $hosted=@(Get-MgTeamChannelMessageHostedContent -TeamId $fixture.team -ChannelId $fixture.channel -ChatMessageId '0' -All)
    if($hosted.Count -ne 1 -or $hosted[0].Id -ne 'hosted-one'){throw 'Hosted content discovery mismatch'}
    $hostedUri="$($config.endpoints.graph)/v1.0/teams/$($fixture.team)/channels/$([uri]::EscapeDataString($fixture.channel))/messages/0/hostedContents/hosted-one/`$value"
    $binary=Invoke-MgGraphRequest -Uri $hostedUri -OutputType HttpResponseMessage
    try { [IO.File]::WriteAllBytes('/state/powershell-hosted.bin', $binary.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()) }
    finally { $binary.Dispose() }
    if((Get-FileHash /state/powershell-hosted.bin -Algorithm SHA256).Hash.ToLowerInvariant() -cne $config.content_sha256){throw 'Hosted bytes mismatch'}
    $page=Invoke-MgGraphRequest -Uri "$chatPath`?`$top=25"
    @{link=$page.'@odata.nextLink'} | ConvertTo-Json | Set-Content /state/powershell-page.json
    @{client='Microsoft Graph PowerShell';version='2.35.1';channel_messages=101;chat_messages=101;replies=201;hosted_sha256=$config.content_sha256} | ConvertTo-Json | Set-Content /state/powershell-report.json
} else {
    $link=(Get-Content /state/powershell-page.json -Raw | ConvertFrom-Json).link
    $rows=[Collections.Generic.List[object]]::new()
    do {
        $page=Invoke-MgGraphRequest -Uri $link
        foreach($row in $page.value){$rows.Add($row)}
        $link=$page.'@odata.nextLink'
    } while($link)
    if($rows.Count -ne 76 -or @($rows | Where-Object {$_.body.content -ne 'Local evidence'}).Count -ne 0){throw 'Saved snapshot changed across restart'}
    $edited=Get-MgChatMessage -ChatId $fixture.chat -ChatMessageId '1'
    if($edited.Body.Content -ne 'Edited after snapshot'){throw 'Current message edit missing'}
    $report=Get-Content /state/powershell-report.json -Raw | ConvertFrom-Json -AsHashtable
    $report.snapshot_after_restart=$true
    $report | ConvertTo-Json | Set-Content /state/powershell-report.json
}
Disconnect-MgGraph | Out-Null

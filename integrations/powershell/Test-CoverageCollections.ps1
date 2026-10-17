$ErrorActionPreference='Stop'
. /project/integrations/powershell/Connect-MockAzure.ps1
Connect-MockAzure -ConfigPath /state/client.json
Import-Module Microsoft.Graph.Applications -RequiredVersion 2.35.1
Import-Module Microsoft.Graph.Groups -RequiredVersion 2.35.1
$config=Get-Content /state/client.json -Raw | ConvertFrom-Json
$apps=@(Get-MgApplication -All -PageSize 1 -Property id,web,passwordCredentials,keyCredentials -CountVariable applicationCount -ConsistencyLevel eventual)
if($apps.Count -ne $config.protocol_application_count -or $applicationCount -ne $apps.Count){throw 'Application count/paging/retry mismatch'}
foreach($app in $apps){
    foreach($credential in $app.PasswordCredentials){
        if($credential.SecretText){throw 'Credential secret leaked in selected projection'}
    }
}
$group=Get-MgGroup -GroupId $config.protocol_parent_id -Property id -ExpandProperty 'members($select=id,displayName)'
$ids=@($group.Members | ForEach-Object {$_.Id})
if($ids.Count -ne 20){throw 'Expansion was not bounded at twenty members'}
if($group.AdditionalProperties['members@odata.nextLink']){throw 'Directory expansion must not expose a nested nextLink'}
$members=@(Get-MgGroupMember -GroupId $config.protocol_parent_id -All -PageSize 10 -Property id,displayName)
$ids=@($members | ForEach-Object {$_.Id})
if(@(Compare-Object ($config.protocol_member_ids | Sort-Object) ($ids | Sort-Object)).Count -or $ids.Count -ne 23){throw 'Membership collection mismatch'}
$uri=$config.endpoints.graph + '/v1.0/$batch'
$body=@{requests=@(
    @{id='object';method='GET';url=('/directoryObjects/'+$config.user_id)},
    @{id='apps';method='GET';url='/applications?$select=id'},
    @{id='devices';method='GET';url='/devices'},
    @{id='denied';method='GET';url='/security/alerts_v2'},
    @{id='invalid';method='GET';url='/applications?$select=secretText'}
)} | ConvertTo-Json -Depth 10
$batch=Invoke-MgGraphRequest -Method POST -Uri $uri -Body $body -ContentType application/json
$expected=@{object=200;apps=200;devices=200;denied=403;invalid=400}
foreach($item in $batch.responses){if($item.status -ne $expected[$item.id]){throw ('Batch item mismatch: '+$item.id)}}
if($batch.responses.Count -ne 5){throw 'Batch item count mismatch'}
@{client='Microsoft Graph PowerShell';version='2.35.1';application_count=$apps.Count;application_page_size=1;injected_continuation_retry=$true;expanded_members=20;relationship_members=$ids.Count;relationship_page_size=10;batch_items=$batch.responses.Count;credential_secrets_absent=$true} | ConvertTo-Json | Set-Content /state/protocol-report.json
Disconnect-MgGraph | Out-Null

param([string]$ConfigPath='/state/client.json')
$ErrorActionPreference='Stop'
. /project/integrations/powershell/Connect-MockAzure.ps1
$config=Get-Content $ConfigPath -Raw | ConvertFrom-Json
Import-Module Microsoft.Graph.Mail -RequiredVersion 2.35.1
function Assert-Mail($value,$message){if(-not $value){throw $message}}
function Invoke-Audit($body){
    $root="$($config.endpoints.graph)/v1.0/security/auditLog/queries"
    $created=Invoke-MgGraphRequest -Headers $script:graphHeaders -Method POST -Uri $root -Body ($body | ConvertTo-Json -Depth 20) -ContentType 'application/json'
    Assert-Mail ($created.id -and $created.status -eq 'notStarted') 'Query creation contract mismatch'
    $uri="$root/$($created.id)"
    for($attempt=0;$attempt -lt 120;$attempt++){
        $query=Invoke-MgGraphRequest -Headers $script:graphHeaders -Method GET -Uri $uri
        if($query.status -eq 'succeeded'){break}
        Assert-Mail ($query.status -in @('notStarted','running')) 'Audit job failed'
        Start-Sleep -Milliseconds 500
    }
    Assert-Mail ($query.status -eq 'succeeded') 'Audit job timed out'
    $records=[System.Collections.Generic.List[object]]::new()
    $next="$uri/records";$pages=0
    while($next){
        $page=Invoke-MgGraphRequest -Headers $script:graphHeaders -Method GET -Uri $next
        foreach($row in $page.value){$records.Add($row)}
        $next=$page.'@odata.nextLink';$pages++
        Assert-Mail ($pages -le 100) 'Unbounded audit pages'
    }
    return @{records=@($records.ToArray());pages=$pages}
}
$result=[ordered]@{passed=$false;client='Microsoft Graph PowerShell 2.35.1';modes=[ordered]@{}}
foreach($mode in @('application','delegated')){
    if($mode -eq 'application'){Connect-MockAzure -ConfigPath $ConfigPath}
    else{Connect-MockAzure -ConfigPath $ConfigPath -Mode AuthorizationCode -TokenFile /state/delegated-token.json}
    $localToken=Get-Content "/state/$mode-token.json" -Raw | ConvertFrom-Json
    $script:graphHeaders=@{Authorization="Bearer $($localToken.access_token)"}
    $checks=[ordered]@{}
    $started=[datetime]::UtcNow.AddSeconds(-1).ToString('o')
    $rule=New-MgUserMailFolderMessageRule -UserId $config.user_id -MailFolderId inbox -BodyParameter @{
        displayName="PowerShell rule $mode";sequence=0;isEnabled=$true
        actions=@{forwardTo=@(@{emailAddress=@{address='sdk-target@example.test'}});stopProcessingRules=$true}
    }
    $loaded=Get-MgUserMailFolderMessageRule -UserId $config.user_id -MailFolderId inbox -MessageRuleId $rule.Id
    Assert-Mail ($loaded.Actions.ForwardTo[0].EmailAddress.Address -eq 'sdk-target@example.test' -and $loaded.Actions.StopProcessingRules) 'Rule action mismatch'
    $checks.rule_actions=$true
    Update-MgUserMailFolderMessageRule -UserId $config.user_id -MailFolderId inbox -MessageRuleId $rule.Id -BodyParameter @{displayName="Updated $mode";isEnabled=$false}
    Assert-Mail (-not (Get-MgUserMailFolderMessageRule -UserId $config.user_id -MailFolderId inbox -MessageRuleId $rule.Id).IsEnabled) 'Rule update missing'
    Remove-MgUserMailFolderMessageRule -UserId $config.user_id -MailFolderId inbox -MessageRuleId $rule.Id
    $rows=@(Get-MgUserMailFolderMessageRule -UserId $config.user_id -MailFolderId inbox -All -PageSize 1)
    Assert-Mail ($rows.Count -eq 3 -and @($rows | Where-Object {$_.Id -notin $config.mail_fixture.rule_ids}).Count -eq 0) 'Rule paging/deletion mismatch'
    $checks.rule_crud=$true;$checks.rule_paging=$true
    $body=@{displayName="PowerShell UAL $mode";serviceFilter='Exchange';operationFilters=@('MailItemsAccessed')
        filterStartDateTime=$config.mail_fixture.audit_start;filterEndDateTime=$config.mail_fixture.audit_end}
    $query=Invoke-Audit $body
    Assert-Mail ($query.pages -gt 1 -and $query.records.Count -eq 1003) 'Audit page count mismatch'
    Assert-Mail (@($query.records | Where-Object {$_.id -notin $config.mail_fixture.audit_ids -or $_.operation -ne 'MailItemsAccessed'}).Count -eq 0) 'Audit identity/operation mismatch'
    Assert-Mail (@($query.records.id | Sort-Object -Unique).Count -eq 1003) 'Duplicate audit IDs'
    $checks.audit_query=$true;$checks.audit_paging=$true
    $body.objectIdFilters=@($config.mail_fixture.audit_object)
    $exact=Invoke-Audit $body
    Assert-Mail ($exact.records.Count -eq 1 -and $exact.records[0].id -eq $config.mail_fixture.audit_ids[0]) 'Audit exact filter mismatch'
    $checks.audit_filters=$true
    for($attempt=0;$attempt -lt 30;$attempt++){
        $native=Invoke-Audit @{
            displayName='Native rule evidence';operationFilters=@('New-InboxRule','Set-InboxRule','Remove-InboxRule')
            objectIdFilters=@("$($config.mailbox_upn)\$($rule.Id)")
            filterStartDateTime=$started;filterEndDateTime=[datetime]::UtcNow.ToString('o')
        }
        if($native.records.Count -eq 3){break}
        Start-Sleep -Milliseconds 500
    }
    Assert-Mail ($native.records.Count -eq 3 -and @($native.records.operation | Sort-Object -Unique).Count -eq 3) 'Native audit projection missing'
    $checks.native_evidence=$true
    Disconnect-MgGraph | Out-Null
    $token=Get-Content "/state/denied_$mode-token.json" -Raw | ConvertFrom-Json
    Connect-MgGraph -AccessToken (ConvertTo-SecureString $token.access_token -AsPlainText -Force) -Environment MockAzure -NoWelcome
    $script:graphHeaders=@{Authorization="Bearer $($token.access_token)"}
    foreach($uri in @("$($config.endpoints.graph)/v1.0/security/auditLog/queries",
                      "$($config.endpoints.graph)/v1.0/users/$($config.user_id)/mailFolders/inbox/messageRules")){
        $denied=$false
        try{Invoke-MgGraphRequest -Headers $script:graphHeaders -Method GET -Uri $uri | Out-Null}catch{
            $denied=($_.ErrorDetails.Message -match 'HTTP/1.1 403|Authorization_RequestDenied|ErrorAccessDenied')
            if($_.Exception.Response){$denied=([int]$_.Exception.Response.StatusCode -eq 403)}
        }
        Assert-Mail $denied 'Missing permission denial'
    }
    $checks.permission_denied=$true
    Disconnect-MgGraph | Out-Null
    $result.modes[$mode]=$checks
}
$result.passed=$true
$result | ConvertTo-Json -Depth 10 | Set-Content /state/graph-mail.json
$result | ConvertTo-Json -Depth 10

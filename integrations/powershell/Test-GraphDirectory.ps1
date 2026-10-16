param([string]$ConfigPath = '/state/client.json')
$ErrorActionPreference = 'Stop'
. /project/integrations/powershell/Connect-MockAzure.ps1
$config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
Import-Module Microsoft.Graph.Users -RequiredVersion 2.35.1
Import-Module Microsoft.Graph.Groups -RequiredVersion 2.35.1

function Assert-True($Value, $Message) {
    if (-not $Value) { throw $Message }
}
function Read-Delta([string]$Uri) {
    $rows = [System.Collections.Generic.List[object]]::new()
    $pages = 0
    do {
        $page = Invoke-MgGraphRequest -Method GET -Uri $Uri -OutputType Hashtable -Headers $script:graphHeaders
        foreach ($row in $page.value) { $rows.Add($row) }
        $Uri = $page['@odata.nextLink']
        $pages++
        Assert-True ($pages -lt 150) 'Delta paging did not finish'
    } while ($Uri)
    Assert-True ($null -ne $page['@odata.deltaLink']) 'Missing delta link'
    return @{ rows = $rows.ToArray(); link = $page['@odata.deltaLink'] }
}
$result = [ordered]@{ passed = $false; client = 'Microsoft Graph PowerShell 2.35.1'; modes = [ordered]@{} }
foreach ($mode in @('application', 'delegated')) {
    if ($mode -eq 'application') {
        Connect-MockAzure -ConfigPath $ConfigPath
    } else {
        Connect-MockAzure -ConfigPath $ConfigPath -Mode AuthorizationCode -TokenFile /state/delegated-token.json
    }
    $localToken = Get-Content "/state/$mode-token.json" -Raw | ConvertFrom-Json
    $script:graphHeaders = @{Authorization = "Bearer $($localToken.access_token)"}
    $users = @(Get-MgUser -Filter "startsWith(userPrincipalName,'seed')" -Property id,displayName -All -PageSize 333)
    Assert-True ($users.Count -eq 5000) 'Expected 5,000 seeded users'
    Assert-True (($users.Id -join ',') -ceq ($config.seed_ids -join ',')) 'Seed page order or identities differ'
    $suffix = [guid]::NewGuid().ToString('N').Substring(0,12)
    $userId = $groupId = $null
    try {
        $user = New-MgUser -BodyParameter @{
            accountEnabled = $true; displayName = 'PowerShell analyst'; mailNickname = "sdk$suffix"
            userPrincipalName = "sdk$suffix@$($config.domain)"
            passwordProfile = @{password = [guid]::NewGuid().ToString('N'); forceChangePasswordNextSignIn = $false}
        }
        $userId = $user.Id
        Assert-True ((Get-MgUser -UserId $userId).DisplayName -eq 'PowerShell analyst') 'User read differs'
        $group = New-MgGroup -BodyParameter @{
            displayName = "PowerShell security $suffix"; mailNickname = "sdk$suffix"
            mailEnabled = $false; securityEnabled = $true; groupTypes = @()
        }
        $groupId = $group.Id
        # Generated cmdlet bootstrap is exercised. It exposes objects, not delta links;
        # use the module's supported Invoke-MgGraphRequest for opaque link traversal.
        $generated = @(Get-MgGroupDelta -All)
        Assert-True ($generated.Id -contains $groupId) 'Generated group delta missing created group'
        $initial = Read-Delta "$($config.endpoints.graph)/v1.0/groups/delta"
        Update-MgUser -UserId $userId -BodyParameter @{displayName = 'PowerShell updated'}
        Assert-True ((Get-MgUser -UserId $userId).DisplayName -eq 'PowerShell updated') 'Update not visible'
        New-MgGroupMemberByRef -GroupId $groupId -BodyParameter @{
            '@odata.id' = "$($config.endpoints.graph)/v1.0/directoryObjects/$userId"
        }
        $members = @(Get-MgGroupMember -GroupId $groupId)
        Assert-True ($members.Count -eq 1 -and $members[0].Id -eq $userId) 'Membership read differs'
        $changes = Read-Delta $initial.link
        $changed = @($changes.rows | Where-Object { $_.id -eq $groupId })[0]
        Assert-True ($changed['members@delta'].id -contains $userId) 'Membership delta missing'
        Remove-MgGroupMemberByRef -GroupId $groupId -DirectoryObjectId $userId
        Assert-True (@(Get-MgGroupMember -GroupId $groupId).Count -eq 0) 'Membership removal failed'
        Remove-MgUser -UserId $userId
        $userId = $null
        Remove-MgGroup -GroupId $groupId
        $deleted = Read-Delta $changes.link
        $removed = @($deleted.rows | Where-Object { $_.id -eq $groupId })[0]
        Assert-True ($removed['@removed'].reason -eq 'deleted') 'Deletion delta missing'
        $groupId = $null
    } finally {
        if ($groupId) { Remove-MgGroup -GroupId $groupId }
        if ($userId) { Remove-MgUser -UserId $userId }
        Disconnect-MgGraph | Out-Null
    }
    $token = Get-Content "/state/denied_$mode-token.json" -Raw | ConvertFrom-Json
    Connect-MgGraph -AccessToken (ConvertTo-SecureString $token.access_token -AsPlainText -Force) -Environment MockAzure -NoWelcome
    $denied = $false
    try { Get-MgUser -Top 1 | Out-Null } catch {
        $denied = $_.Exception.Message -match '403|Authorization_RequestDenied|Insufficient privileges'
        if (-not $denied) { throw }
    } finally { Disconnect-MgGraph | Out-Null }
    Assert-True $denied 'Missing Graph permission accepted'
    $result.modes[$mode] = [ordered]@{
        seed_user_count = $users.Count; seed_first_id = $users[0].Id
        create = $true; profile_update = $true; membership = $true
        delta = $true; delete = $true; permission_denied = $denied
    }
}
$result.passed = $true
$result | ConvertTo-Json -Depth 8 | Set-Content $config.output_file
$result | ConvertTo-Json -Depth 8

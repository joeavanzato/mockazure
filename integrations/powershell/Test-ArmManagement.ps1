$ErrorActionPreference = 'Stop'
$config = Get-Content /state/client.json -Raw | ConvertFrom-Json
. /project/integrations/powershell/Connect-ArmLocal.ps1
Import-Module Az.Resources -RequiredVersion 7.8.0
$result = @{passed=$false;versions=@{'Az.Accounts'='4.0.2';'Az.Resources'='7.8.0'};modes=@{}}
try {
    foreach ($mode in @('application','delegated')) {
        Connect-LocalArm -Config $config -Mode $mode
        $checks = @{}
        $subscriptions = @(Get-AzSubscription -TenantId $config.tenant_id)
        if ($subscriptions.Count -ne 1 -or $subscriptions[0].Id -ne $config.subscription_id) {
            throw 'Subscription discovery mismatch'
        }
        $checks.discovery=$true
        $resources = @(Get-AzResource)
        if ($resources.Count -ne 103) {throw "Inventory mismatch: $($resources.Count)"}
        $checks.inventory=$true
        $name = "ps-$mode-$([guid]::NewGuid().ToString('N').Substring(0,8))"
        $group = New-AzResourceGroup -Name $name -Location eastus -Tag @{Created=$mode}
        Set-AzResourceGroup -Name $name -Tag @{Updated=$mode} | Out-Null
        if ((Get-AzResourceGroup -Name $name).Tags.Updated -ne $mode) {throw 'Group update mismatch'}
        $scope = "/subscriptions/$($config.subscription_id)/resourceGroups/$name"
        New-AzTag -ResourceId $scope -Tag @{Evidence=$mode} | Out-Null
        Update-AzTag -ResourceId $scope -Operation Merge -Tag @{More='yes'} | Out-Null
        $tags = Get-AzTag -ResourceId $scope
        if ($tags.Properties.TagsProperty.More -ne 'yes') {throw 'Tag merge mismatch'}
        Remove-AzTag -ResourceId $scope -Confirm:$false | Out-Null
        $checks.tag_crud=$true
        $definitions = @(Get-AzRoleDefinition)
        if ($definitions.Count -ne 20) {throw 'Built-in role definitions mismatch'}
        $reader = $definitions | Where-Object Name -eq 'Reader'
        $checks.role_definitions=$true
        $assignment = New-AzRoleAssignment -ObjectType User -ObjectId $config.negative_user_id -RoleDefinitionId $reader.Id -Scope $scope
        if (@(Get-AzRoleAssignment -Scope $scope | Where-Object ObjectId -eq $config.negative_user_id).Count -ne 1) {
            throw 'Role assignment mismatch'
        }
        Remove-AzRoleAssignment -ObjectId $config.negative_user_id -RoleDefinitionId $reader.Id -Scope $scope | Out-Null
        $checks.role_assignments=$true
        Remove-AzResourceGroup -Name $name -Force | Out-Null
        $checks.group_crud=$true
        $filter = "eventTimestamp ge '$($config.arm_fixture.start)' and eventTimestamp le '$($config.arm_fixture.end)'"
        $path = "$($config.endpoints.arm)/subscriptions/$($config.subscription_id)/providers/Microsoft.Insights/eventtypes/management/values"
        $url = $path + '?api-version=2015-04-01&$filter=' + [uri]::EscapeDataString($filter)
        $rows = [Collections.Generic.List[object]]::new()
        $pages=0
        do {
            if (([uri]$url).GetLeftPart([UriPartial]::Authority) -cne $config.endpoints.arm) {
                throw 'ARM continuation escaped local origin'
            }
            $page = (Invoke-AzRestMethod -Uri $url -Method GET).Content | ConvertFrom-Json -DateKind String
            $rows.AddRange([object[]]$page.value)
            $url = $page.nextLink
            $pages++
        } while ($url)
        if ($rows.Count -ne 1003 -or $pages -lt 11 -or @($rows.eventDataId | Sort-Object -Unique).Count -ne 1003) {
            throw 'Activity paging lost or duplicated records'
        }
        $expected = Get-Content /state/arm-expected.json -Raw | ConvertFrom-Json -DateKind String
        $lookup=@{};foreach($item in $rows){$lookup[$item.eventDataId]=$item}
        foreach($item in $expected){
            $actual=$lookup[$item.eventDataId]
            if(-not $actual -or $actual.id -cne $item.id -or $actual.eventTimestamp -cne $item.eventTimestamp -or
               $actual.submissionTimestamp -cne $item.submissionTimestamp){throw 'Activity exact identity/time mismatch'}
        }
        $checks.activity_paging=$true
        $selected = (Invoke-AzRestMethod -Method GET -Uri ($path +
            '?api-version=2015-04-01&$filter=' + [uri]::EscapeDataString($filter+" and resourceGroupName eq 'evidence'") +
            '&$select=id,eventTimestamp')).Content | ConvertFrom-Json -DateKind String
        if($selected.value.Count -ne 100 -or $selected.value[0].PSObject.Properties.Name.Count -ne 2){
            throw 'Activity filter/select mismatch'
        }
        $checks.activity_filters=$true
        Connect-LocalArm -Config $config -Mode $mode -Denied
        $denied=Invoke-AzRestMethod -Method GET -Uri ($path+'?api-version=2015-04-01&$filter='+[uri]::EscapeDataString($filter))
        if($denied.StatusCode -ne 403){throw 'Activity permission denial mismatch'}
        $checks.permission_denied=$true
        $result.modes[$mode]=$checks
    }
    $result.passed=$true
} catch {
    $result.error_type=$_.Exception.GetType().FullName
    $result.error=$_.Exception.Message
    $result.arm_error=$_.Exception.Body
    $result.stack=$_.ScriptStackTrace
    $result.exception_stack=$_.Exception.StackTrace
} finally {
    $result | ConvertTo-Json -Depth 20 | Set-Content /state/arm.json
    $result | ConvertTo-Json -Depth 20
}
if(-not $result.passed){exit 1}

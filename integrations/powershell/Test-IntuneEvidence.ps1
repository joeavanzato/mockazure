param([ValidateSet('prepare','resume')][string]$Phase='prepare')
$ErrorActionPreference='Stop'
. /project/integrations/powershell/Connect-MockAzure.ps1
Connect-MockAzure -ConfigPath /state/client.json
Import-Module Microsoft.Graph.DeviceManagement -RequiredVersion 2.35.1
Import-Module Microsoft.Graph.Devices.CorporateManagement -RequiredVersion 2.35.1
$config=Get-Content /state/client.json -Raw | ConvertFrom-Json
. /project/integrations/powershell/Graph-LocalTransport.ps1 -LocalOrigin $config.endpoints.graph
$path="$($config.endpoints.graph)/v1.0/deviceManagement/managedDevices"
if($Phase -eq 'prepare') {
    $devices=@(Get-MgDeviceManagementManagedDevice -All -PageSize 25)
    $userDevices=@(Get-MgUserManagedDevice -UserId $config.user_id -All)
    $detected=@(Get-MgDeviceManagementDetectedAppManagedDevice -DetectedAppId 'detected' -All)
    if($devices.Count -ne 101 -or $userDevices.Count -ne 101 -or $detected.Count -ne 101){throw 'Inventory pagination mismatch'}
    $configurations=@(Get-MgDeviceManagementDeviceConfiguration -All)
    $policies=@(Get-MgDeviceManagementDeviceCompliancePolicy -All)
    if($configurations.Count -ne 4 -or $policies.Count -ne 4){throw 'Platform policy mismatch'}
    $statuses=0
    foreach($item in $configurations) {
        $read=Get-MgDeviceManagementDeviceConfiguration -DeviceConfigurationId $item.Id
        $assignments=@(Get-MgDeviceManagementDeviceConfigurationAssignment -DeviceConfigurationId $item.Id -All)
        if($read.Id -ne $item.Id -or $assignments.Count -ne 1){throw 'Configuration identity mismatch'}
        $statuses+=@(Get-MgDeviceManagementDeviceConfigurationDeviceStatus -DeviceConfigurationId $item.Id -All -PageSize 25).Count
    }
    foreach($item in $policies) {
        $read=Get-MgDeviceManagementDeviceCompliancePolicy -DeviceCompliancePolicyId $item.Id
        $assignments=@(Get-MgDeviceManagementDeviceCompliancePolicyAssignment -DeviceCompliancePolicyId $item.Id -All)
        if($read.Id -ne $item.Id -or $assignments.Count -ne 1){throw 'Compliance policy identity mismatch'}
        $statuses+=@(Get-MgDeviceManagementDeviceCompliancePolicyDeviceStatus -DeviceCompliancePolicyId $item.Id -All -PageSize 25).Count
    }
    if($statuses -ne 108){throw 'Status pagination mismatch'}
    $page=Invoke-MgGraphRequest -Uri "$path`?`$top=25"
    @{link=$page.'@odata.nextLink'} | ConvertTo-Json | Set-Content /state/powershell-page.json
    @{client='Microsoft Graph PowerShell';version='2.35.1';managed_devices=101;platform_policies=8;statuses=$statuses} | ConvertTo-Json | Set-Content /state/powershell-report.json
} else {
    $link=(Get-Content /state/powershell-page.json -Raw | ConvertFrom-Json).link
    $rows=[Collections.Generic.List[object]]::new()
    do {
        $page=Invoke-MgGraphRequest -Uri $link
        foreach($row in $page.value){$rows.Add($row)}
        $link=$page.'@odata.nextLink'
    } while($link)
    if($rows.Count -ne 76 -or @($rows | Where-Object {$_.deviceName -ne 'Before'}).Count -ne 0){throw 'Saved inventory snapshot changed'}
    $edited=Get-MgDeviceManagementManagedDevice -ManagedDeviceId 'm050'
    if($edited.DeviceName -ne 'After'){throw 'Current observation edit missing'}
    $report=Get-Content /state/powershell-report.json -Raw | ConvertFrom-Json -AsHashtable
    $report.snapshot_after_restart=$true
    $report | ConvertTo-Json | Set-Content /state/powershell-report.json
}
Disconnect-MgGraph | Out-Null

param([string]$ConfigPath = '/state/client.json')
$ErrorActionPreference = 'Stop'
. /project/integrations/powershell/Connect-MockAzure.ps1
$config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
foreach ($module in @('Microsoft.Graph.Reports','Microsoft.Graph.Identity.SignIns','Microsoft.Graph.Users.Actions','Microsoft.Graph.Users')) {
    Import-Module $module -RequiredVersion 2.35.1
}
function Assert-True($Value, $Message) {
    if (-not $Value) { throw $Message }
}
$result = [ordered]@{passed=$false;client='Microsoft Graph PowerShell 2.35.1';modes=[ordered]@{}}
foreach ($mode in @('application','delegated')) {
    if ($mode -eq 'application') {
        Connect-MockAzure -ConfigPath $ConfigPath
    } else {
        Connect-MockAzure -ConfigPath $ConfigPath -Mode AuthorizationCode -TokenFile /state/delegated-token.json
    }
    try {
        $signins = @(Get-MgAuditLogSignIn -Filter 'createdDateTime ge 2026-10-07T00:00:00Z and createdDateTime lt 2026-10-08T00:00:00Z' -All -PageSize 333)
        $audits = @(Get-MgAuditLogDirectoryAudit -Filter 'activityDateTime ge 2026-10-07T00:00:00Z and activityDateTime lt 2026-10-08T00:00:00Z' -All -PageSize 333)
        Assert-True ((($signins.Id | Sort-Object) -join ',') -ceq ($config.evidence_ids.signIn -join ',')) 'Sign-in IDs differ'
        Assert-True ((($audits.Id | Sort-Object) -join ',') -ceq ($config.evidence_ids.directoryAudit -join ',')) 'Audit IDs differ'
        $detections = @(Get-MgRiskDetection -All)
        $users = @(Get-MgRiskyUser -All)
        Assert-True ($detections.Count -gt 0 -and $users.Id -contains $config.user_id) 'Risk reads missing'
        Confirm-MgRiskyUserCompromised -BodyParameter @{userIds=@($config.user_id)}
        Assert-True ((Get-MgRiskyUser -RiskyUserId $config.user_id).RiskState -eq 'confirmedCompromised') 'Confirmation not visible'
        Invoke-MgDismissRiskyUser -BodyParameter @{userIds=@($config.user_id)}
        Assert-True ((Get-MgRiskyUser -RiskyUserId $config.user_id).RiskState -eq 'dismissed') 'Dismissal not visible'
        Update-MgUser -UserId $config.seed_first_id -BodyParameter @{usageLocation='US'}
        $assigned = Set-MgUserLicense -UserId $config.seed_first_id -BodyParameter @{
            addLicenses=@(@{skuId=$config.sku_id;disabledPlans=@()});removeLicenses=@()
        }
        Assert-True ($assigned.AssignedLicenses.SkuId -contains $config.sku_id) 'Licence assignment missing'
        $appDenied = $false
        try {
            $details = @(Get-MgUserLicenseDetail -UserId $config.seed_first_id -All)
            Assert-True ($mode -eq 'delegated' -and $details.Count -gt 0) 'Delegated licence details missing'
        } catch {
            if ($mode -ne 'application' -or $_.Exception.Message -notmatch '403|Authorization_RequestDenied') { throw }
            $appDenied=$true
        }
        Set-MgUserLicense -UserId $config.seed_first_id -BodyParameter @{addLicenses=@();removeLicenses=@($config.sku_id)} | Out-Null
        $result.modes[$mode]=[ordered]@{
            signIn_count=$signins.Count;directoryAudit_count=$audits.Count;exact_ids=$true
            risk_reads=$true;confirm=$true;dismiss=$true;license_add_remove=$true
            license_details=($mode -eq 'delegated');license_details_app_denied=$appDenied
        }
    } finally { Disconnect-MgGraph | Out-Null }
    $token=Get-Content "/state/denied_$mode-token.json" -Raw | ConvertFrom-Json
    Connect-MgGraph -AccessToken (ConvertTo-SecureString $token.access_token -AsPlainText -Force) -Environment MockAzure -NoWelcome
    $denied=$false
    try { Get-MgAuditLogDirectoryAudit -Top 1 | Out-Null } catch {
        $denied=$_.Exception.Message -match '403|Authorization_RequestDenied'
        if (-not $denied) { throw }
    } finally { Disconnect-MgGraph | Out-Null }
    Assert-True $denied 'Missing audit permission accepted'
    $result.modes[$mode].permission_denied=$denied
}
$result.passed=$true
$result | ConvertTo-Json -Depth 8 | Set-Content $config.output_file
@{passed=$true;client=$result.client} | ConvertTo-Json

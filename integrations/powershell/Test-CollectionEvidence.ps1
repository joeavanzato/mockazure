$ErrorActionPreference='Stop'
. /project/integrations/powershell/Connect-MockAzure.ps1
Connect-MockAzure -ConfigPath /state/client.json
Import-Module Microsoft.Graph.Security -RequiredVersion 2.35.1
$checks=@{}
foreach($case in @(
    @{name='alerts';command='Get-MgSecurityAlert';count=301},
    @{name='alerts_v2';command='Get-MgSecurityAlertV2';count=301},
    @{name='incidents';command='Get-MgSecurityIncident';count=5},
    @{name='secureScores';command='Get-MgSecuritySecureScore';count=3},
    @{name='secureScoreControlProfiles';command='Get-MgSecuritySecureScoreControlProfile';count=101}
)){
    $rows=@(& $case.command -All -PageSize 40)
    if($rows.Count -ne $case.count -or @($rows.Id | Select-Object -Unique).Count -ne $case.count){throw "Incomplete SDK collection: $($case.name)"}
    $checks[$case.name]=$rows.Count
}
$typed=Get-MgSecurityAlertV2 -AlertId alert-000
if($typed.Evidence[0].AdditionalProperties.userAccount.userPrincipalName -ne 'analyst@contoso.test'){
    # Some generated versions expose the derived model directly.
    if($typed.Evidence[0].UserAccount.UserPrincipalName -ne 'analyst@contoso.test'){throw 'Typed alert evidence lost'}
}
$checks.typed_user_evidence=$true
$root='/vendor/collectors/Microsoft-Extractor-Suite/Microsoft-Extractor-Suite-47565f7842784cd3667ff630e3dccb49fa3408ed'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile("$root/Microsoft-Extractor-Suite.psm1",[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Original MES parse failed'}
$source=($ast.EndBlock.Statements | Where-Object {$_ -is [System.Management.Automation.Language.FunctionDefinitionAst] -or $_ -is [System.Management.Automation.Language.TypeDefinitionAst]} | ForEach-Object {$_.Extent.Text}) -join [Environment]::NewLine
$module=New-Module -Name OriginalMESCollection -ArgumentList $root,$source -ScriptBlock {
    param($root,$source)
    . ([scriptblock]::Create($source))
    . "$root/Scripts/Get-SecurityAlerts.ps1"
    . "$root/Scripts/Get-SecureScore.ps1"
    $script:logFile="/state/collector.log"
    function Connect-MgGraph {throw 'Original collector attempted an unconfigured reconnect'}
    Export-ModuleMember -Function Get-SecurityAlerts,Get-SecureScore
}
Import-Module $module
New-Item -ItemType Directory /state/alerts,/state/scores -Force | Out-Null
Get-SecurityAlerts -OutputDir /state/alerts -DaysBack 0 -LogLevel Standard
Get-SecureScore -OutputDir /state/scores -LogLevel Standard
$alerts=Get-ChildItem /state/alerts -Filter *.csv -Recurse | ForEach-Object {Import-Csv $_.FullName}
$scores=Get-ChildItem /state/scores -Filter *.csv -Recurse | ForEach-Object {Import-Csv $_.FullName}
if(@($alerts).Count -ne 301 -or @($alerts.Id | Select-Object -Unique).Count -ne 301){throw 'MES alert CSV IDs incomplete'}
if(@($alerts | Where-Object {$_.Id -notlike 'legacy-*' -or $_.AffectedUser -ne 'analyst@contoso.test/null'}).Count){throw 'MES alert fields did not match stored evidence'}
if(@($scores).Count -ne 101 -or @($scores | Where-Object {[double]$_.CurrentScore -ne 1.25 -or [double]$_.MaxScore -ne 2.0}).Count){throw 'MES Secure Score join incomplete'}
$checks.mes_original_alerts_csv=301
$checks.mes_original_secure_score_csv=101
$hashes=@{}
foreach($path in @('Microsoft-Extractor-Suite.psm1','Scripts/Get-SecurityAlerts.ps1','Scripts/Get-SecureScore.ps1')){
    $hashes[$path]=(Get-FileHash "$root/$path" -Algorithm SHA256).Hash.ToLowerInvariant()
}
@{client='Microsoft.Graph.Security';version=(Get-Module Microsoft.Graph.Security).Version.ToString();checks=$checks;source_hashes=$hashes;upstream_notes=@('Get-SecurityAlerts tests graphAuth.Type, while original helper returns AuthType; this original invocation therefore selects legacy alerts. Modern alerts are independently checked through the SDK.')} | ConvertTo-Json -Depth 10 | Set-Content /state/powershell-report.json
Disconnect-MgGraph | Out-Null

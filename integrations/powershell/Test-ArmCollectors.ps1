param([Parameter(Mandatory)][string]$CasePath)
$ErrorActionPreference='Stop'
[cultureinfo]::CurrentCulture=[cultureinfo]::GetCultureInfo('en-US')
[cultureinfo]::CurrentUICulture=[cultureinfo]::GetCultureInfo('en-US')
if([TimeZoneInfo]::Local.Id -notin @('UTC','Etc/UTC')){throw 'ARM collector qualification requires UTC'}
$case=Get-Content $CasePath -Raw | ConvertFrom-Json -AsHashtable -DateKind String
$config=Get-Content /state/client.json -Raw | ConvertFrom-Json
. /project/integrations/powershell/Connect-ArmLocal.ps1
$root='/vendor/collectors/Microsoft-Extractor-Suite/Microsoft-Extractor-Suite-47565f7842784cd3667ff630e3dccb49fa3408ed'
$hashes=@{}
$report=@{mode=$case.mode;discovery=$case.discovery;denied=$case.denied;invoked=$false
    source_hashes=$hashes;files=@();requests=@();error=$null}
New-Item -ItemType Directory -Path $case.output_dir -Force | Out-Null
try {
    Connect-LocalArm -Config $config -Mode $case.mode -Denied:$case.denied
    if($case.denied){
        $filter="eventTimestamp ge '$($config.arm_fixture.start)'"
        $uri=$config.endpoints.arm+"/subscriptions/"+$config.subscription_id+
            "/providers/Microsoft.Insights/eventtypes/management/values?api-version=2015-04-01&"+'$filter='+[uri]::EscapeDataString($filter)
        $preflight=Invoke-AzRestMethod -Method GET -Uri $uri
        $report.denied_preflight_status=[int]$preflight.StatusCode
        if($report.denied_preflight_status -ne 403){throw 'Missing local Activity permission denial'}
    }
    $tokens=$null;$errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseFile("$root/Microsoft-Extractor-Suite.psm1",[ref]$tokens,[ref]$errors)
    if($errors.Count){throw 'Original MES module parse failed'}
    $declarations=$ast.EndBlock.Statements | Where-Object {
        $_ -is [Management.Automation.Language.FunctionDefinitionAst] -or
        $_ -is [Management.Automation.Language.TypeDefinitionAst] -or
        $_.Extent.Text -ceq '$script:LogLevel = [LogLevel]::Standard'
    }
    $source=($declarations | ForEach-Object {$_.Extent.Text}) -join [Environment]::NewLine
    $module=New-Module -Name OriginalMESArm -ArgumentList $source,$root,$config.endpoints.arm,$case.output_dir -ScriptBlock {
        param($source,$root,$origin,$output)
        . ([scriptblock]::Create($source))
        . "$root/Scripts/Get-AzureActivityLogs.ps1"
        . /project/integrations/powershell/Arm-LocalTransport.ps1 -LocalOrigin $origin
        $script:logFile=Join-Path $output 'collector.log'
        Export-ModuleMember -Function Get-ActivityLogs,Get-MockAzureTransportRequests
    }
    foreach($path in @('Microsoft-Extractor-Suite.psm1','Scripts/Get-AzureActivityLogs.ps1')){
        $hashes["$root/$path"]=(Get-FileHash "$root/$path" -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    Import-Module $module
    $parameters=@{OutputDir=$case.output_dir;LogLevel='Standard'
        StartDate=$config.arm_fixture.start;EndDate=$config.arm_fixture.end}
    if($case.discovery -eq 'explicit'){$parameters.SubscriptionID=$config.subscription_id}
    $report.invoked=$true
    if(-not $case.denied -and $case.native_start){
        $parameters.OutputDir=Join-Path $case.output_dir 'historical'
        New-Item -ItemType Directory -Path $parameters.OutputDir -Force | Out-Null
    }
    Get-ActivityLogs @parameters
    if(-not $case.denied -and $case.native_start){
        $parameters.OutputDir=Join-Path $case.output_dir 'native'
        New-Item -ItemType Directory -Path $parameters.OutputDir -Force | Out-Null
        $parameters.StartDate=$case.native_start
        $parameters.EndDate=$case.native_end
        Get-ActivityLogs @parameters
    }
} catch {
    $report.error=$_.Exception.Message
    $report.error_type=$_.Exception.GetType().FullName
    $report.stack=$_.ScriptStackTrace
} finally {
    if(Get-Command Get-MockAzureTransportRequests -ErrorAction SilentlyContinue){
        $report.requests=@(Get-MockAzureTransportRequests)
    }
    $report.files=@(Get-ChildItem $case.output_dir -Recurse -File | ForEach-Object {$_.FullName})
    $report | ConvertTo-Json -Depth 20 | Set-Content $case.report
}
if($report.error -and -not $case.denied){exit 1}

param([Parameter(Mandatory)][string]$CasePath)
$ErrorActionPreference='Stop'
$case=Get-Content $CasePath -Raw | ConvertFrom-Json -AsHashtable
$config=Get-Content /state/client.json -Raw | ConvertFrom-Json
. /project/integrations/powershell/Connect-MockAzure.ps1
if($case.mode -eq 'application'){Connect-MockAzure -ConfigPath /state/client.json}
else {Connect-MockAzure -ConfigPath /state/client.json -Mode AuthorizationCode -TokenFile /state/delegated-token.json}
if($case.denial -eq 'permission'){
    Disconnect-MgGraph | Out-Null
    $denied=Get-Content ("/state/denied_"+$case.mode+"-token.json") -Raw | ConvertFrom-Json
    Microsoft.Graph.Authentication\Connect-MgGraph -AccessToken (ConvertTo-SecureString $denied.access_token -AsPlainText -Force) -Environment MockAzure -NoWelcome
}
foreach($name in @('Microsoft.Graph.Reports','Microsoft.Graph.Users','Microsoft.Graph.Applications',
    'Microsoft.Graph.Identity.Governance','Microsoft.Graph.Identity.DirectoryManagement','Microsoft.Graph.Identity.SignIns')){
    Import-Module $name -RequiredVersion 2.35.1
}
$hashes=@{}
$telemetry=[Collections.Generic.List[string]]::new()
$root=if($case.collector -eq 'hawk'){
    '/vendor/collectors/hawk/hawk-a44536f2b4225dd8ea2e463db88d5db40cfac8c7/Hawk'
}else{
    '/vendor/collectors/Microsoft-Extractor-Suite/Microsoft-Extractor-Suite-47565f7842784cd3667ff630e3dccb49fa3408ed'
}
New-Item -ItemType Directory -Path $case.output_dir -Force | Out-Null
try {
    if($case.collector -eq 'hawk'){
        Import-Module PSFramework -RequiredVersion 1.12.346
        $paths=@('internal/functions/Write-HawkBanner.ps1','internal/functions/Write-HawkConfigurationComplete.ps1','internal/functions/Test-UserObject.ps1',
            'internal/functions/Test-HawkGlobalObject.ps1','internal/functions/Test-GraphConnection.ps1',
            'internal/functions/Initialize-HawkGlobalObject.ps1','internal/functions/Out-LogFile.ps1',
            'internal/functions/Out-MultipleFileType.ps1','functions/Tenant/Get-HawkTenantAppAndSPNCredentialDetail.ps1')
        $module=New-Module -Name OriginalHawkSecurity -ArgumentList $root,$paths,$telemetry -ScriptBlock {
            param($root,$paths,$telemetry)
            $script:telemetry=$telemetry
            foreach($path in $paths){. "$root/$path"}
            # Named telemetry-only boundary: no service query or serializer is replaced.
            function New-AIClient {param($key) $script:telemetry.Add('New-AIClient'); return [pscustomobject]@{disabled=$true}}
            function Send-AIEvent {param($Event) $script:telemetry.Add('Send-AIEvent')}
            function Connect-MgGraph {throw 'Original Hawk attempted an unconfigured reconnect'}
            Export-ModuleMember -Function Initialize-HawkGlobalObject,Get-HawkTenantAppAndSPNCredentialDetail
        }
        foreach($path in $paths){$hashes["$root/$path"]=(Get-FileHash "$root/$path" -Algorithm SHA256).Hash.ToLowerInvariant()}
        Import-Module $module
        Initialize-HawkGlobalObject -StartDate $case.start -EndDate $case.end -FilePath $case.output_dir -SkipUpdate -NonInteractive -Force
        Get-HawkTenantAppAndSPNCredentialDetail
    }else{
        $tokens=$null;$errors=$null
        $ast=[System.Management.Automation.Language.Parser]::ParseFile("$root/Microsoft-Extractor-Suite.psm1",[ref]$tokens,[ref]$errors)
        if($errors.Count){throw 'Original MES module parse failed'}
        # Exact original declarations omit only module-level UI/update execution.
        $declarations=$ast.EndBlock.Statements | Where-Object {
            $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] -or
            $_ -is [System.Management.Automation.Language.TypeDefinitionAst]
        }
        $source=($declarations | ForEach-Object {$_.Extent.Text}) -join [Environment]::NewLine
        $scriptPath = switch ($case.function) {
            'Get-ConditionalAccessPolicies' {'Scripts/Get-ConditionalAccessPolicy.ps1'}
            'Get-OAuthPermissionsGraph' {'Scripts/Get-OAuthPermissions.ps1'}
            'Get-MFA' {'Scripts/Get-MFAStatus.ps1'}
            'Get-AllRoleActivity' {'Scripts/Get-Roles.ps1'}
            default {throw 'Unqualified MES function'}
        }
        $module=New-Module -Name OriginalMESSecurity -ArgumentList $source,$root,$config.endpoints.graph,$case.output_dir,$scriptPath -ScriptBlock {
            param($source,$root,$origin,$output,$scriptPath)
            . ([scriptblock]::Create($source))
            . "$root/$scriptPath"
            . /project/integrations/powershell/Graph-LocalTransport.ps1 -LocalOrigin $origin
            $script:logFile=Join-Path $output 'collector.log'
            function Connect-MgGraph {throw 'Original MES attempted an unconfigured reconnect'}
            Export-ModuleMember -Function Get-ConditionalAccessPolicies,Get-OAuthPermissionsGraph,Get-MFA,Get-AllRoleActivity
        }
        foreach($path in @('Microsoft-Extractor-Suite.psm1',$scriptPath)){
            $hashes["$root/$path"]=(Get-FileHash "$root/$path" -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        Import-Module $module
        $parameters=@{OutputDir=$case.output_dir;LogLevel='Standard'}
        if($case.function -eq 'Get-MFA'){
            $parameters.UserIds=@((Get-Content $config.user_file -Raw | ConvertFrom-Json).username)
        }
        & $case.function @parameters
    }
    $errorText=$null
}catch{
    $errorText=$_.Exception.Message
}finally{
    $report=@{mode=$case.mode;collector=$case.collector;function=$case.function;parameters=$case;
        source_hashes=$hashes;telemetry_calls=@($telemetry.ToArray());error=$errorText;
        files=@(Get-ChildItem $case.output_dir -Recurse -File | ForEach-Object {$_.FullName})}
    $report | ConvertTo-Json -Depth 12 | Set-Content $case.report
    Disconnect-MgGraph | Out-Null
}
if($errorText){throw $errorText}

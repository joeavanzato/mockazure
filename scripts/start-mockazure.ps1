[CmdletBinding()]
param(
    [ValidateRange(1, 65529)][int]$Port = 8443,
    [ValidateRange(1, 65535)][int]$DbPort = 55432,
    [switch]$SkipBuild,
    [switch]$NoBrowser,
    [switch]$NoPasswordPrompt,
    [switch]$SetPassword,
    [switch]$TrustCa,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($Help) {
    Write-Host @'
start-mockazure.bat [-Port 8443] [-DbPort 55432] [-SkipBuild]
                   [-NoBrowser] [-NoPasswordPrompt] [-SetPassword] [-TrustCa]

Builds and starts PostgreSQL, migrations, identity initialization, API, worker
and HTTPS ingress, then opens the packaged web UI. Docker Desktop must be running
with Linux containers. Existing configuration and database storage are reused.
Port options apply to first initialization; conflicting existing settings fail.
First startup prompts for a shared lab password. -SetPassword resets it explicitly.
-NoPasswordPrompt starts services without setting a missing console password.
-TrustCa explicitly installs this lab's CA in your Windows CurrentUser Root store.
-SkipBuild reuses mockazure:local; omit it after pulling application changes.
'@
    exit 0
}

function Invoke-LabDocker {
    param([string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed (exit $LASTEXITCODE). Check the output above."
    }
}

$labRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $labRoot
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Docker was not found. Install Docker Desktop and enable Linux containers.'
    }
    $dockerOs = & docker info --format '{{.OSType}}'
    if ($LASTEXITCODE -ne 0 -or "$dockerOs".Trim() -ne 'linux') {
        throw 'Start Docker Desktop with Linux containers, then run this launcher again.'
    }
    Invoke-LabDocker -Arguments @('compose', 'version')
    $labHost = 'localhost'
    $labBind = '127.0.0.1'
    if (Test-Path -LiteralPath '.runtime/compose.env') {
        $labEnvironment = @{}
        foreach ($line in Get-Content -LiteralPath '.runtime/compose.env') {
            if ($line -match '^([A-Z_]+)=(.*)$') { $labEnvironment[$Matches[1]] = $Matches[2] }
        }
        foreach ($setting in @(@('Port', 'MOCKAZURE_API_PORT'), @('DbPort', 'MOCKAZURE_DB_PORT'))) {
            $saved = [int]$labEnvironment[$setting[1]]
            if ($saved -lt 1) { throw 'Existing compose.env is missing a required port.' }
            if ($PSBoundParameters.ContainsKey($setting[0]) -and $PSBoundParameters[$setting[0]] -ne $saved) {
                throw "$($setting[0]) differs from this instance. Reuse its existing configuration."
            }
        }
        $Port = [int]$labEnvironment['MOCKAZURE_API_PORT']
        $DbPort = [int]$labEnvironment['MOCKAZURE_DB_PORT']
        if ($labEnvironment.ContainsKey('MOCKAZURE_BIND_ADDRESS')) {
            $labBind = $labEnvironment['MOCKAZURE_BIND_ADDRESS']
        }
        $labConfig = Get-Content -LiteralPath '.runtime/instance/native.json' -Raw | ConvertFrom-Json
        $labHost = ([uri]$labConfig.admin_base_url).DnsSafeHost
    }
    if (-not $SkipBuild) {
        Write-Host 'Building MockAzure from this checkout...'
        Invoke-LabDocker -Arguments @('build', '-t', 'mockazure:local', '.')
    }
    Write-Host 'Initializing or validating the existing instance...'
    Invoke-LabDocker -Arguments @('run', '--rm', '--network', 'none', '--mount',
        "type=bind,source=$labRoot,target=/project", 'mockazure:local', 'init', '--compose',
        '--project-dir', '/project', '--port', "$Port", '--db-port', "$DbPort",
        '--advertised-host', $labHost, '--bind-address', $labBind)
    $compose = @('compose', '--project-directory', $labRoot, '-f', (Join-Path $labRoot 'compose.yaml'),
        '--env-file', (Join-Path $labRoot '.runtime/compose.env'))
    Write-Host 'Starting the complete stack and waiting for readiness...'
    Invoke-LabDocker -Arguments ($compose + @('up', '-d', '--wait', '--wait-timeout', '300'))
    $passwordState = & docker @compose exec -T db psql -U mockazure -d mockazure -tAc 'SELECT EXISTS (SELECT 1 FROM console_credential WHERE id=1)'
    if ($LASTEXITCODE -ne 0 -or "$passwordState".Trim() -notin @('t', 'f')) {
        throw 'Could not check console password setup after database migration.'
    }
    if ($SetPassword -or ("$passwordState".Trim() -eq 'f' -and -not $NoPasswordPrompt)) {
        Write-Host 'Choose a shared lab password (at least 12 UTF-8 bytes).'
        Invoke-LabDocker -Arguments ($compose + @('exec', 'api', 'mockazure', 'console', 'password', 'set', '--config', '/instance/container.json'))
    } elseif ("$passwordState".Trim() -eq 'f') {
        Write-Warning 'Console password is not set. Run start-mockazure.bat normally to configure it.'
    }
    $labCa = Join-Path $labRoot '.runtime/instance/ca.pem'
    if ($TrustCa) {
        Import-Certificate -FilePath $labCa -CertStoreLocation 'Cert:\CurrentUser\Root' | Out-Null
        Write-Host 'This lab CA is trusted for your Windows account.'
    } else {
        Write-Host 'If your browser does not trust this lab CA, rerun with -TrustCa or import it manually:'
        Write-Host $labCa
    }
    $labUrl = "https://${labHost}:$Port/_mock/ui/"
    Invoke-LabDocker -Arguments ($compose + @('ps'))
    Write-Host "`nMockAzure web UI: $labUrl"
    Write-Host 'Stop without deleting data: docker compose --env-file .runtime/compose.env stop'
    if (-not $NoBrowser) { Start-Process $labUrl }
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
} finally {
    Pop-Location
}

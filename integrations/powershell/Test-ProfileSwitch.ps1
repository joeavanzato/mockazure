$ErrorActionPreference = 'Stop'
. /project/integrations/powershell/Connect-MockAzure.ps1
Connect-MockAzure -ConfigPath /state/client.json
$original = Get-MgEnvironment -Name MockAzure
foreach ($endpoint in @('login', 'graph')) {
    $changed = Get-Content /state/client.json -Raw | ConvertFrom-Json
    $changed.endpoints.$endpoint = 'https://other.mockazure.test'
    # A mismatched configuration must be rejected before reading any credential.
    $changed.secret_file = '/tmp/must-not-read-credentials.json'
    $path = "/tmp/changed-$endpoint.json"
    $changed | ConvertTo-Json -Depth 8 | Set-Content $path
    $blocked = $false
    try {
        Connect-MockAzure -ConfigPath $path
    } catch {
        $blocked = $_.Exception.Message -like '*MockAzure endpoint profile mismatch*'
    }
    if (-not $blocked) { throw "Profile mismatch was not safely blocked for $endpoint" }
}
$current = Get-MgEnvironment -Name MockAzure
if ($current.AzureADEndpoint -ne $original.AzureADEndpoint -or $current.GraphEndpoint -ne $original.GraphEndpoint) {
    throw 'Rejected configuration changed the active environment'
}
Connect-MockAzure -ConfigPath /state/client.json
Disconnect-MgGraph | Out-Null
@{ passed = $true; login_mismatch_blocked = $true; graph_mismatch_blocked = $true } |
    ConvertTo-Json | Set-Content /state/profile-switch.json
Write-Output 'Profile-switch regression passed'

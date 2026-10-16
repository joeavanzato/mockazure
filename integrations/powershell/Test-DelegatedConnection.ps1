$ErrorActionPreference = 'Stop'
. /project/integrations/powershell/Connect-MockAzure.ps1
$config = Get-Content /state/client.json -Raw | ConvertFrom-Json
$probes = @()
foreach ($flow in @(@('Device','/state/device-token.json'), @('AuthorizationCode','/state/authorization-code-token.json'))) {
    Connect-MockAzure -ConfigPath /state/client.json -Mode $flow[0] -TokenFile $flow[1]
    $organizations = @(Get-MgOrganization -ErrorAction Stop)
    $localToken = (Get-Content $flow[1] -Raw | ConvertFrom-Json).access_token
    $me = Invoke-MgGraphRequest -Headers @{ Authorization = "Bearer $localToken" } -Method GET -Uri "$($config.endpoints.graph)/v1.0/me"
    if ($organizations.Count -ne 1 -or $organizations[0].Id -ne $config.tenant_id -or $me.id -ne $config.user_id) {
        throw 'Delegated Graph identity mismatch'
    }
    $probes += @{flow=$flow[0]; passed=$true; user_id=$me.id}
}
$secret = (Get-Content $config.negative_secret_file -Raw | ConvertFrom-Json).secret
$token = Invoke-RestMethod -Method POST -Uri "$($config.endpoints.login)/$($config.tenant_id)/oauth2/v2.0/token" -Body @{
    grant_type='client_credentials'; client_id=$config.denied_client_id; client_secret=$secret
    scope='https://graph.microsoft.com/.default'
}
Connect-MgGraph -AccessToken (ConvertTo-SecureString $token.access_token -AsPlainText -Force) -Environment MockAzure -NoWelcome
$denied = $false
try { Get-MgOrganization -ErrorAction Stop | Out-Null }
catch { $denied = $_.FullyQualifiedErrorId -match "Authorization_RequestDenied"; if (-not $denied) { @{ error_type=$_.Exception.GetType().FullName; error_id=$_.FullyQualifiedErrorId; properties=@($_.Exception.PSObject.Properties.Name) } | ConvertTo-Json } }
if (-not $denied) { throw 'Missing Graph permission was not rejected with 403' }
$report = @{schema_version=1; client='Graph PowerShell'; passed=$true; authentication='Explicit local MSAL token'; probes=$probes; denied_code="Authorization_RequestDenied"}
$report | ConvertTo-Json -Depth 5 | Set-Content /state/delegated.json
$report | ConvertTo-Json -Depth 5

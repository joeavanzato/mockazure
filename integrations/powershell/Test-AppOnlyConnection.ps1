$ErrorActionPreference = 'Stop'
$config = Get-Content /state/client.json -Raw | ConvertFrom-Json
$secret = (Get-Content $config.secret_file -Raw | ConvertFrom-Json).secret
$env:SSL_CERT_FILE = $config.ca_file
$result = [ordered]@{ powershell = $PSVersionTable.PSVersion.ToString() }
Import-Module Microsoft.Graph.Authentication -RequiredVersion 2.35.1
Import-Module Microsoft.Graph.Identity.DirectoryManagement -RequiredVersion 2.35.1
Add-MgEnvironment -Name MockAzure -AzureADEndpoint $config.endpoints.login -GraphEndpoint $config.endpoints.graph | Out-Null
$credential = [pscredential]::new($config.client_id, (ConvertTo-SecureString $secret -AsPlainText -Force))
try {
    Connect-MgGraph -TenantId $config.tenant_id -ClientSecretCredential $credential -Environment MockAzure -ContextScope Process -NoWelcome
    $result.graph_authentication = 'SDK client credentials'
} catch {
    $result.graph_native_error_type = $_.Exception.GetType().FullName
    $token = Invoke-RestMethod -Method POST -Uri "$($config.endpoints.login)/$($config.tenant_id)/oauth2/v2.0/token" -Body @{
        grant_type='client_credentials'; client_id=$config.client_id; client_secret=$secret
        scope='https://graph.microsoft.com/.default'
    }
    Connect-MgGraph -AccessToken (ConvertTo-SecureString $token.access_token -AsPlainText -Force) -Environment MockAzure -NoWelcome
    $result.graph_authentication = 'Explicit local token'
}
$organizations = @(Get-MgOrganization -ErrorAction Stop)
if ($organizations.Count -ne 1 -or $organizations[0].Id -ne $config.tenant_id) {
    throw 'Graph PowerShell returned unexpected tenant identity'
}
$result.graph_organization_id = $organizations[0].Id
$result.graph_passed = $true
$result | ConvertTo-Json | Set-Content /state/app-only.json
$result | ConvertTo-Json

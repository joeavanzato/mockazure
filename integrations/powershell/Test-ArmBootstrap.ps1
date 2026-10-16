$ErrorActionPreference = 'Stop'
$config = Get-Content /state/client.json -Raw | ConvertFrom-Json
$secret = (Get-Content $config.secret_file -Raw | ConvertFrom-Json).secret
$env:SSL_CERT_FILE = $config.ca_file
$result = [ordered]@{ client='Az.Accounts'; version='4.0.2'; passed=$false }
try {
    Import-Module Az.Accounts -RequiredVersion 4.0.2
    Disable-AzContextAutosave -Scope Process | Out-Null
    Update-AzConfig -EnableDataCollection $false -CheckForUpgrade $false -Scope Process | Out-Null
    Add-AzEnvironment -Name MockAzure -ActiveDirectoryAuthority $config.endpoints.login -ResourceManagerUrl $config.endpoints.arm -ActiveDirectoryServiceEndpointResourceId 'https://management.azure.com/' -GraphUrl $config.endpoints.graph -GraphEndpointResourceId 'https://graph.microsoft.com/' | Out-Null
    $token = Invoke-RestMethod -Method POST -Uri "$($config.endpoints.login)/$($config.tenant_id)/oauth2/v2.0/token" -Body @{
        grant_type='client_credentials'; client_id=$config.client_id; client_secret=$secret
        scope='https://management.azure.com/.default'
    }
    $connection = Connect-AzAccount -AccessToken $token.access_token -AccountId $config.client_id -Tenant $config.tenant_id -Environment MockAzure -Scope Process
    $subscriptions = @(Get-AzSubscription -TenantId $config.tenant_id -ErrorAction Stop)
    $result.subscription_ids = @($subscriptions | ForEach-Object { $_.Id })
    $result.tenant_id = [string]$connection.Context.Tenant.Id
    $result.passed = $result.tenant_id -eq $config.tenant_id -and $subscriptions.Count -gt 0
} catch {
    $result.error_type = $_.Exception.GetType().FullName
    $result.error_category = [string]$_.CategoryInfo.Category
}
$result | ConvertTo-Json | Set-Content /state/arm-bootstrap.json
$result | ConvertTo-Json
if (-not $result.passed) { exit 1 }

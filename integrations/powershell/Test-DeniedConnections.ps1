$ErrorActionPreference = 'Stop'
$config = Get-Content /state/client.json -Raw | ConvertFrom-Json
$secret = (Get-Content $config.negative_secret_file -Raw | ConvertFrom-Json).secret
$env:SSL_CERT_FILE = $config.ca_file
$invalidRejected = $false
try {
    Invoke-RestMethod -Method POST -Uri "$($config.endpoints.login)/$($config.tenant_id)/oauth2/v2.0/token" -Body @{
        grant_type='client_credentials'; client_id=$config.client_id; client_secret='invalid-local-credential'
        scope='https://graph.microsoft.com/.default'
    } | Out-Null
} catch { $invalidRejected = [int]$_.Exception.Response.StatusCode -eq 401 }
if (-not $invalidRejected) { throw 'Invalid local credential was not rejected' }
Import-Module ExchangeOnlineManagement -RequiredVersion 3.11.0
$token = Invoke-RestMethod -Method POST -Uri "$($config.endpoints.login)/$($config.tenant_id)/oauth2/v2.0/token" -Body @{
    grant_type='client_credentials'; client_id=$config.denied_client_id; client_secret=$secret
    scope='https://outlook.office365.com/.default'
}
$exchangeRejected = $false
try {
    Connect-ExchangeOnline -AccessToken $token.access_token -AppId $config.denied_client_id -Organization $config.tenant_domain -ConnectionUri $config.endpoints.exchange -AzureADAuthorizationEndpointUri "$($config.endpoints.login)/organizations" -CommandName Get-OrganizationConfig -ShowBanner:$false -SkipLoadingFormatData -SkipLoadingCmdletHelp -DisableWAM -EnableErrorReporting:$false -LogDirectoryPath /state/exo-logs
} catch { $exchangeRejected = $true }
if (-not $exchangeRejected) { throw 'Exchange connection accepted an ungranted application' }
Import-Module Az.Accounts -RequiredVersion 4.0.2
Disable-AzContextAutosave -Scope Process | Out-Null
Update-AzConfig -EnableDataCollection $false -CheckForUpgrade $false -Scope Process | Out-Null
Add-AzEnvironment -Name MockAzure -ActiveDirectoryAuthority $config.endpoints.login -ResourceManagerUrl $config.endpoints.arm -ActiveDirectoryServiceEndpointResourceId 'https://management.azure.com/' -GraphUrl $config.endpoints.graph -GraphEndpointResourceId 'https://graph.microsoft.com/' | Out-Null
$token = Invoke-RestMethod -Method POST -Uri "$($config.endpoints.login)/$($config.tenant_id)/oauth2/v2.0/token" -Body @{
    grant_type='client_credentials'; client_id=$config.denied_client_id; client_secret=$secret
    scope='https://management.azure.com/.default'
}
Connect-AzAccount -AccessToken $token.access_token -AccountId $config.denied_client_id -Tenant $config.tenant_id -Environment MockAzure -Scope Process | Out-Null
$subscriptions = @(Get-AzSubscription -TenantId $config.tenant_id -ErrorAction Stop)
if ($subscriptions.Count -ne 0) { throw 'ARM returned a subscription without an assignment' }
$report = @{schema_version=1; passed=$true; invalid_credential_rejected=$true;
            exchange_ungranted_connection_rejected=$true; arm_unassigned_inventory_empty=$true}
$report | ConvertTo-Json | Set-Content /state/denied-connections.json
$report | ConvertTo-Json

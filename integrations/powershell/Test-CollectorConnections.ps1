$ErrorActionPreference = 'Stop'
$config = Get-Content /state/client.json -Raw | ConvertFrom-Json
$secret = (Get-Content $config.secret_file -Raw | ConvertFrom-Json).secret
$env:SSL_CERT_FILE = $config.ca_file
. /project/integrations/powershell/Connect-MockAzure.ps1
Connect-MockAzure -ConfigPath /state/client.json -Mode Application
Import-Module ExchangeOnlineManagement -RequiredVersion 3.11.0
Import-Module Az.Accounts -RequiredVersion 4.0.2
Disable-AzContextAutosave -Scope Process | Out-Null
Update-AzConfig -EnableDataCollection $false -CheckForUpgrade $false -Scope Process | Out-Null
Add-AzEnvironment -Name MockAzure -ActiveDirectoryAuthority $config.endpoints.login -ResourceManagerUrl $config.endpoints.arm -ActiveDirectoryServiceEndpointResourceId 'https://management.azure.com/' -GraphUrl $config.endpoints.graph -GraphEndpointResourceId 'https://graph.microsoft.com/' | Out-Null
$hawk = '/vendor/collectors/hawk/hawk-a44536f2b4225dd8ea2e463db88d5db40cfac8c7/Hawk/internal/functions'
. "$hawk/Test-GraphConnection.ps1"
. "$hawk/Test-EXOConnection.ps1"
. '/vendor/collectors/Microsoft-Extractor-Suite/Microsoft-Extractor-Suite-47565f7842784cd3667ff630e3dccb49fa3408ed/Scripts/Connect.ps1'
# The update lookup belongs to online preparation. No service cmdlet is replaced.
$script:versionCheckCalls = 0
function versionCheck { $script:versionCheckCalls++ }
function LocalToken([string]$scope) {
    (Invoke-RestMethod -Method POST -Uri "$($config.endpoints.login)/$($config.tenant_id)/oauth2/v2.0/token" -Body @{
        grant_type='client_credentials'; client_id=$config.client_id; client_secret=$secret; scope=$scope
    }).access_token
}
$exchange = LocalToken 'https://outlook.office365.com/.default'
Connect-M365 -AccessToken $exchange -AppId $config.client_id -Organization $config.tenant_domain -ConnectionUri $config.endpoints.exchange -AzureADAuthorizationEndpointUri "$($config.endpoints.login)/organizations" -CommandName Get-OrganizationConfig -ShowBanner:$false -SkipLoadingFormatData -SkipLoadingCmdletHelp -EnableErrorReporting:$false -LogDirectoryPath /state/exo-logs
Test-GraphConnection
Test-EXOConnection
$organization = Get-OrganizationConfig -ErrorAction Stop
$graph = @(Get-MgOrganization -ErrorAction Stop)
if ([string]$organization.Identity -ne $config.tenant_id -or $graph[0].Id -ne $config.tenant_id) {
    throw 'Collector organization mismatch'
}
$arm = LocalToken 'https://management.azure.com/.default'
Connect-AzureAZ -AccessToken $arm -AccountId $config.client_id -Tenant $config.tenant_id -Environment MockAzure -Scope Process
$subscriptions = @(Get-AzSubscription -TenantId $config.tenant_id -ErrorAction Stop)
if ($subscriptions.Count -ne 1 -or $subscriptions[0].Id -ne $config.subscription_id -or $script:versionCheckCalls -ne 2) {
    throw 'Collector ARM discovery mismatch'
}
$report = @{
    schema_version=1; passed=$true; authentication='Explicit local resource tokens'
    hawk_revision='a44536f2b4225dd8ea2e463db88d5db40cfac8c7'
    hawk_functions=@('Test-GraphConnection','Test-EXOConnection')
    mes_revision='47565f7842784cd3667ff630e3dccb49fa3408ed'
    mes_functions=@('Connect-M365','Connect-AzureAZ')
    organization_id=$config.tenant_id; subscription_id=$config.subscription_id
    limitations=@('Hawk connection functions only; pre-established local contexts',
                  'MES update lookup replaced by preparation-only gate',
                  'No collection workflow qualification')
}
$report | ConvertTo-Json -Depth 5 | Set-Content /state/collectors.json
$report | ConvertTo-Json -Depth 5

$ErrorActionPreference = 'Stop'
$config = Get-Content /state/client.json -Raw | ConvertFrom-Json
$secret = (Get-Content $config.secret_file -Raw | ConvertFrom-Json).secret
$env:SSL_CERT_FILE = $config.ca_file
$result = [ordered]@{client='ExchangeOnlineManagement'; version='3.11.0'; passed=$false}
try {
    Import-Module ExchangeOnlineManagement -RequiredVersion 3.11.0
    $token = Invoke-RestMethod -Method POST -Uri "$($config.endpoints.login)/$($config.tenant_id)/oauth2/v2.0/token" -Body @{
        grant_type='client_credentials'; client_id=$config.client_id; client_secret=$secret
        scope='https://outlook.office365.com/.default'
    }
    Connect-ExchangeOnline -AccessToken $token.access_token -AppId $config.client_id -Organization $config.tenant_domain -ConnectionUri $config.endpoints.exchange -AzureADAuthorizationEndpointUri "$($config.endpoints.login)/organizations" -CommandName Get-OrganizationConfig -ShowBanner:$false -SkipLoadingFormatData -SkipLoadingCmdletHelp -DisableWAM -EnableErrorReporting:$false -LogDirectoryPath /state/exo-logs
    $organization = Get-OrganizationConfig -ErrorAction Stop
    $result.passed = $true
    $result.organization_identity = [string]$organization.Identity
} catch {
    # Never print exception arguments, token contents or verbose SDK traces.
    $result.error_type = $_.Exception.GetType().FullName
    $result.error_category = [string]$_.CategoryInfo.Category
}
$result | ConvertTo-Json | Set-Content /state/exchange-bootstrap.json
$result | ConvertTo-Json
if (-not $result.passed) { exit 1 }

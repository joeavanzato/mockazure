function Connect-MockAzure {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$ConfigPath,
        [ValidateSet('Application','Device','AuthorizationCode')][string]$Mode = 'Application',
        [string]$TokenFile
    )
    $config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    $env:SSL_CERT_FILE = $config.ca_file
    Import-Module Microsoft.Graph.Authentication -RequiredVersion 2.35.1
    Import-Module Microsoft.Graph.Identity.DirectoryManagement -RequiredVersion 2.35.1
    if (-not (Get-MgEnvironment -Name MockAzure -ErrorAction SilentlyContinue)) {
        Add-MgEnvironment -Name MockAzure -AzureADEndpoint $config.endpoints.login -GraphEndpoint $config.endpoints.graph | Out-Null
    }
    if ($Mode -eq 'Application') {
        $secret = (Get-Content $config.secret_file -Raw | ConvertFrom-Json).secret
        $token = Invoke-RestMethod -Method POST -Uri "$($config.endpoints.login)/$($config.tenant_id)/oauth2/v2.0/token" -Body @{
            grant_type='client_credentials'; client_id=$config.client_id; client_secret=$secret
            scope='https://graph.microsoft.com/.default'
        }
    } else {
        if (-not $TokenFile) { throw 'Supply a locally acquired MSAL token file for the selected delegated flow.' }
        $token = Get-Content $TokenFile -Raw | ConvertFrom-Json
        if ($token.flow -ne $Mode) { throw 'Token file does not match the requested local flow.' }
    }
    Connect-MgGraph -AccessToken (ConvertTo-SecureString $token.access_token -AsPlainText -Force) -Environment MockAzure -NoWelcome
}

function Connect-LocalArm {
    param([Parameter(Mandatory)]$Config, [Parameter(Mandatory)][string]$Mode, [switch]$Denied)
    Import-Module Az.Accounts -RequiredVersion 4.0.2
    Disable-AzContextAutosave -Scope Process | Out-Null
    Update-AzConfig -EnableDataCollection $false -CheckForUpgrade $false -Scope Process | Out-Null
    $origin = [uri]$Config.endpoints.arm
    if ($origin.Scheme -ne 'https' -or $origin.Host -ne 'management.mockazure.test') {
        throw 'This container qualification requires the isolated local ARM origin'
    }
    $environment = @{Name='MockAzure';ActiveDirectoryAuthority=$Config.endpoints.login
        ResourceManagerUrl=$Config.endpoints.arm
        ActiveDirectoryServiceEndpointResourceId='https://management.azure.com'
        MicrosoftGraphUrl=$Config.endpoints.graph;MicrosoftGraphEndpointResourceId='https://graph.microsoft.com/'
        GraphUrl=$Config.endpoints.graph;GraphEndpointResourceId='https://graph.microsoft.com/'}
    Add-AzEnvironment @environment | Out-Null
    $label = if ($Denied) { "denied_$Mode" } else { $Mode }
    $token = (Get-Content "/state/$label-token.json" -Raw | ConvertFrom-Json).access_token
    $account = if ($Mode -eq 'application') {
        if ($Denied) { $Config.negative_client_id } else { $Config.client_id }
    } else {
        if ($Denied) { 'denied@arm.test' } else { 'owner@arm.test' }
    }
    $connect=@{AccessToken=$token;AccountId=$account;Tenant=$Config.tenant_id
        Environment='MockAzure';Scope='Process';SkipContextPopulation=$true}
    Connect-AzAccount @connect | Out-Null
    if (-not $Denied) {
        Set-AzContext -Subscription $Config.subscription_id -Tenant $Config.tenant_id -Scope Process | Out-Null
    }
}

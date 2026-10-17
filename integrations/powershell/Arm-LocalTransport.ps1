param([Parameter(Mandatory)][string]$LocalOrigin)
$local = [uri]$LocalOrigin
if ($local.Scheme -cne 'https' -or $local.UserInfo -or $local.Query -or $local.Fragment -or
    $local.AbsolutePath -ne '/' -or $local.Host -cne 'management.mockazure.test') {
    throw 'An explicit isolated local ARM origin is required'
}
$script:MockAzureArmOrigin = $local.GetLeftPart([UriPartial]::Authority)
$script:MockAzureTransportRequests = [Collections.Generic.List[object]]::new()
function script:Invoke-RestMethod {
    [CmdletBinding()]
    param([Parameter(Mandatory)][uri]$Uri, [string]$Method='GET', [hashtable]$Headers)
    if (-not $Uri.IsAbsoluteUri -or $Uri.UserInfo -or $Uri.Fragment) {
        throw 'Collector URI must be absolute and within its local ARM boundary'
    }
    $origin = $Uri.GetLeftPart([UriPartial]::Authority)
    if ($origin -ceq 'https://management.azure.com') {
        $Uri = [uri]($script:MockAzureArmOrigin + $Uri.PathAndQuery)
    } elseif ($origin -cne $script:MockAzureArmOrigin) {
        throw 'Collector request origin is outside the local ARM boundary'
    }
    $script:MockAzureTransportRequests.Add(@{method=$Method;path=$Uri.AbsolutePath;local=$true
        filter_query=$Uri.Query})
    # Preserve the original PowerShell response conversion, headers and method.
    # Only the URL origin and redirect boundary differ.
    Microsoft.PowerShell.Utility\Invoke-RestMethod -Uri $Uri -Method $Method -Headers $Headers -MaximumRedirection 0
}
function script:Get-MockAzureTransportRequests { return $script:MockAzureTransportRequests.ToArray() }
function script:Connect-AzAccount { throw 'Collector attempted an unconfigured reconnect' }

param([Parameter(Mandatory)][string]$LocalOrigin)
$local = [uri]$LocalOrigin
if ($local.Scheme -cne 'https' -or $local.UserInfo -or $local.Query -or $local.Fragment -or $local.AbsolutePath -ne '/' -or
    ($local.Host -notin @('localhost','127.0.0.1') -and -not $local.Host.EndsWith('.mockazure.test'))) {
    throw 'An explicit local HTTPS Graph origin is required'
}
$script:MockAzureGraphOrigin = $local.GetLeftPart([UriPartial]::Authority)
$script:MockAzureTransportRequests = [System.Collections.Generic.List[object]]::new()
# Preserve the pinned SDK's complete authentication/retry pipeline. Its redirect
# option is internal in 2.35.1; fail closed if the verified layout changes.
$existing = [Microsoft.Graph.PowerShell.Authentication.Helpers.HttpHelpers]::GetGraphHttpClient()
$flags = [Reflection.BindingFlags]'Instance,NonPublic'
$field = [System.Net.Http.HttpMessageInvoker].GetField('_handler', $flags)
if (-not $field) { throw 'Pinned SDK handler chain unavailable' }
$handler = $field.GetValue($existing)
$disabled = $false
while ($handler) {
    if ($handler.GetType().FullName -eq 'Microsoft.Graph.PowerShell.Authentication.Handlers.AuthenticationHandler') {
        # Raw Invoke-MgGraphRequest's pinned provider defaults to public Graph hosts
        # despite Add-MgEnvironment. Bind its existing credential to the local host.
        $provider = $handler.GetType().GetProperty('AuthenticationProvider').GetValue($handler)
        $provider.AllowedHostsValidator.AllowedHosts = [string[]]@($local.Host)
    }
    if ($handler.GetType().FullName -eq 'Microsoft.Kiota.Http.HttpClientLibrary.Middleware.RedirectHandler') {
        $option = $handler.GetType().GetField('<RedirectOption>k__BackingField', $flags)
        if (-not $option) { throw 'Pinned SDK redirect option unavailable' }
        $settings = $option.GetValue($handler)
        $settings.MaxRedirect = 0
        if ($settings.MaxRedirect -ne 0) { throw 'Could not disable SDK redirects' }
        $disabled = $true
    }
    if ($handler -is [System.Net.Http.SocketsHttpHandler] -and $handler.AllowAutoRedirect) {
        throw 'Underlying SDK transport permits redirects'
    }
    if ($handler -is [System.Net.Http.DelegatingHandler]) { $handler = $handler.InnerHandler }
    else { break }
}
if (-not $disabled) { throw 'Pinned SDK redirect handler unavailable' }
function script:Connect-MgGraph { throw 'Collector attempted to reconnect; local authentication must be established first' }
function script:Invoke-MgGraphRequest {
    [CmdletBinding()]
    param([Parameter(Mandatory)][uri]$Uri,
          [string]$Method='GET',[object]$Body,[hashtable]$Headers,
          [string]$ContentType,[string]$OutputType)
    if (-not $Uri.IsAbsoluteUri -or $Uri.UserInfo -or $Uri.Fragment) {
        throw 'Collector request must have an absolute permitted URI'
    }
    $origin = $Uri.GetLeftPart([UriPartial]::Authority)
    if ($origin -ceq 'https://graph.microsoft.com') {
        $Uri = [uri]($script:MockAzureGraphOrigin + $Uri.PathAndQuery)
    } elseif ($origin -cne $script:MockAzureGraphOrigin) {
        throw 'Collector request origin is outside the local Graph boundary'
    }
    $request = @{}
    foreach ($key in @('Method','Body','Headers','ContentType','OutputType')) {
        if ($PSBoundParameters.ContainsKey($key)) { $request[$key]=$PSBoundParameters[$key] }
    }
    $request.Uri = $Uri
    $script:MockAzureTransportRequests.Add(@{method=$Method;path=$Uri.AbsolutePath;local=$true})
    $response = Microsoft.Graph.Authentication\Invoke-MgGraphRequest @request -StatusCodeVariable localStatus -ResponseHeadersVariable localHeaders
    if ($localStatus -ge 300 -and $localStatus -lt 400) { throw 'Collector redirect rejected' }
    return $response
}

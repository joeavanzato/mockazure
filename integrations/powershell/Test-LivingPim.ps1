$ErrorActionPreference = 'Stop'
$p = [Console]::In.ReadToEnd() | ConvertFrom-Json
$origin = [uri]$p.endpoint
if ($origin.Scheme -ne 'https' -or $origin.DnsSafeHost -notin @('localhost','127.0.0.1')) {
    throw 'PIM qualification requires loopback HTTPS.'
}
$env:SSL_CERT_FILE = $p.ca_file
if ($p.relay) {
    Add-Type @'
using System.Net;
using System.Net.Sockets;
using System.Threading.Tasks;
public static class PimLoopbackRelay {
    public static void Start(int port) {
        var listener = new TcpListener(IPAddress.Loopback, port);
        listener.Start();
        _ = Task.Run(async () => {
            while (true) {
                var incoming = await listener.AcceptTcpClientAsync();
                _ = Task.Run(async () => {
                    using (incoming)
                    using (var outgoing = new TcpClient()) {
                        await outgoing.ConnectAsync("host.docker.internal", port);
                        await Task.WhenAny(
                            incoming.GetStream().CopyToAsync(outgoing.GetStream()),
                            outgoing.GetStream().CopyToAsync(incoming.GetStream()));
                    }
                });
            }
        });
    }
}
'@
    [PimLoopbackRelay]::Start($origin.Port)
}
$env:PSModulePath = "$($p.vendor)/modules" + [IO.Path]::PathSeparator + $env:PSModulePath
Import-Module Microsoft.Graph.Authentication -RequiredVersion 2.35.1
Import-Module Microsoft.Graph.Identity.Governance -RequiredVersion 2.35.1
Add-MgEnvironment -Name LivingPim -GraphEndpoint $p.endpoint -AzureADEndpoint $p.endpoint | Out-Null
function Set-LocalToken([string]$token) {
    Microsoft.Graph.Authentication\Connect-MgGraph -AccessToken (
        ConvertTo-SecureString $token -AsPlainText -Force
    ) -Environment LivingPim -NoWelcome
}
function Evidence-Status([int]$expected) {
    try {
        $result = Microsoft.Graph.Authentication\Invoke-MgGraphRequest `
            -Uri "$($p.endpoint)/v1.0/auditLogs/directoryAudits" -Method GET
        if ($expected -ne 200) { throw 'Unexpected authorization to audit evidence.' }
    } catch {
        if ($expected -ne 403 -or "$_" -notmatch '403|Authorization_RequestDenied') { throw }
    }
}
Set-LocalToken $p.delegated_token
. "$($p.project)/integrations/powershell/Graph-LocalTransport.ps1" -LocalOrigin $p.endpoint
Evidence-Status 403
Set-LocalToken $p.admin_token
. "$($p.project)/integrations/powershell/Graph-LocalTransport.ps1" -LocalOrigin $p.endpoint
$body = @{
    principalId=$p.user_id; roleDefinitionId=$p.role_id; directoryScopeId='/'
    action='adminAssign'; justification='Native local PIM qualification'
    scheduleInfo=@{expiration=@{type='afterDuration';duration='PT2H'}}
}
$eligible = New-MgRoleManagementDirectoryRoleEligibilityScheduleRequest -BodyParameter $body
if (-not $eligible.Id) { throw 'Original client did not deserialize eligibility request.' }
Set-LocalToken $p.delegated_token
. "$($p.project)/integrations/powershell/Graph-LocalTransport.ps1" -LocalOrigin $p.endpoint
Evidence-Status 403
$body.action='selfActivate'
$body.scheduleInfo.expiration.duration='PT1H'
$active = New-MgRoleManagementDirectoryRoleAssignmentScheduleRequest -BodyParameter $body
if (-not $active.Id) { throw 'Original client did not deserialize activation request.' }
Evidence-Status 200
$body.action='selfDeactivate'
$off = New-MgRoleManagementDirectoryRoleAssignmentScheduleRequest -BodyParameter $body
if (-not $off.Id) { throw 'Original client did not deserialize deactivation request.' }
Evidence-Status 403
[ordered]@{
    state='passed';client='Microsoft.Graph.Identity.Governance';version='2.35.1'
    steps=@('denied','eligible-denied','activated-allowed','deactivated-denied')
    request_ids=@($eligible.Id,$active.Id,$off.Id)
} | ConvertTo-Json -Compress
Microsoft.Graph.Authentication\Disconnect-MgGraph | Out-Null

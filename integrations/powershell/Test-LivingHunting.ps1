$ErrorActionPreference = 'Stop'
$p = [Console]::In.ReadToEnd() | ConvertFrom-Json
$origin = [uri]$p.endpoint
if ($origin.Scheme -ne 'https' -or $origin.DnsSafeHost -notin @('localhost','127.0.0.1')) {
    throw 'Hunting qualification requires loopback HTTPS.'
}
$env:SSL_CERT_FILE = $p.ca_file
if ($p.relay) {
    Add-Type @'
using System.Net;
using System.Net.Sockets;
using System.Threading.Tasks;
public static class HuntingLoopbackRelay {
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
    [HuntingLoopbackRelay]::Start($origin.Port)
}
$env:PSModulePath = "$($p.vendor)/modules" + [IO.Path]::PathSeparator + $env:PSModulePath
Import-Module Microsoft.Graph.Authentication -RequiredVersion 2.35.1
Import-Module Microsoft.Graph.Security -RequiredVersion 2.35.1
Add-MgEnvironment -Name LivingHunting -GraphEndpoint $p.endpoint -AzureADEndpoint $p.endpoint | Out-Null
Connect-MgGraph -AccessToken (ConvertTo-SecureString $p.token -AsPlainText -Force) `
    -Environment LivingHunting -NoWelcome
$result = Start-MgSecurityHuntingQuery -BodyParameter @{
    Query = 'DeviceInfo | project DeviceId, DeviceName'
    Timespan = 'P1D'
}
if ($result.Schema.Count -ne 2 -or $result.Schema[0].Name -ne 'DeviceId' `
    -or $result.Schema[0].Type -ne 'String' -or $result.Results.Count -ne 1) {
    throw 'Original hunting cmdlet did not return the expected schema and row count.'
}
$row = $result.Results[0].AdditionalProperties
if ($row['DeviceId'] -ne $p.device_id) { throw 'Original hunting cmdlet returned the wrong entity.' }
[ordered]@{
    state = 'passed'
    client = 'Microsoft.Graph.Security'
    version = (Get-Module Microsoft.Graph.Security).Version.ToString()
    cmdlet = 'Start-MgSecurityHuntingQuery'
    schema = @($result.Schema | ForEach-Object { @{ name = $_.Name; type = $_.Type } })
    device_id = $row['DeviceId']
} | ConvertTo-Json -Depth 10 -Compress

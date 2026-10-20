$ErrorActionPreference = 'Stop'
$profile = [Console]::In.ReadToEnd() | ConvertFrom-Json
foreach ($url in @($profile.endpoint, $profile.login)) {
    $uri = [uri]$url
    if ($uri.Scheme -ne 'https' -or $uri.DnsSafeHost -notin @('localhost', '127.0.0.1')) {
        throw 'Qualification requires explicit loopback HTTPS endpoints.'
    }
}
$env:SSL_CERT_FILE = $profile.ca_file
if ($profile.relay) {
    # TCP forwarding preserves end-to-end TLS and the original localhost origin.
    # It is confined to this disposable client container, never a host listener.
    Add-Type @'
using System.Net;
using System.Net.Sockets;
using System.Threading.Tasks;
public static class LivingLoopbackRelay {
    public static void Start(int port, int destinationPort) {
        var listener = new TcpListener(IPAddress.Loopback, port);
        listener.Start();
        _ = Task.Run(async () => {
            while (true) {
                var incoming = await listener.AcceptTcpClientAsync();
                _ = Task.Run(async () => {
                    using (incoming)
                    using (var outgoing = new TcpClient()) {
                        await outgoing.ConnectAsync("host.docker.internal", destinationPort);
                        var input = incoming.GetStream();
                        var output = outgoing.GetStream();
                        await Task.WhenAny(input.CopyToAsync(output), output.CopyToAsync(input));
                    }
                });
            }
        });
    }
}
'@
    $destinationPort = ([uri]$profile.endpoint).Port
    [LivingLoopbackRelay]::Start($destinationPort, $destinationPort)
    if ($destinationPort -ne 443) { [LivingLoopbackRelay]::Start(443, $destinationPort) }
}
$env:PSModulePath = (Join-Path $profile.vendor 'modules') + [IO.Path]::PathSeparator + $env:PSModulePath
[Reflection.Assembly]::LoadFrom((Join-Path $profile.vendor 'dependencies/Microsoft.Extensions.DependencyInjection.Abstractions/8.0.0/lib/net8.0/Microsoft.Extensions.DependencyInjection.Abstractions.dll')) | Out-Null
Import-Module ExchangeOnlineManagement -RequiredVersion 3.11.0
Connect-ExchangeOnline -AccessToken $profile.token -ConnectionUri $profile.endpoint -AzureADAuthorizationEndpointUri "$($profile.login)/organizations" -AppId $profile.client_id -Organization $profile.domain -ShowBanner:$false -SkipLoadingFormatData -SkipLoadingCmdletHelp -DisableWAM -EnableErrorReporting:$false -LogDirectoryPath $profile.logs
try {
    if ($profile.audit) {
        $records = @()
        $session = [guid]::NewGuid().ToString()
        for ($page = 0; $page -lt 100; $page++) {
            $batch = @(Search-UnifiedAuditLog -StartDate ([datetime]$profile.start) -EndDate ([datetime]$profile.end) -SessionId $session -SessionCommand ReturnLargeSet -ResultSize 5000)
            $records += $batch
            if ($batch.Count -eq 0) { break }
        }
        if ($page -eq 100) { throw 'Audit collection exceeded bounded paging limit.' }
        $ids = @($records | ForEach-Object { [string](($_.AuditData | ConvertFrom-Json).Id) } | Sort-Object -Unique)
        [ordered]@{state='passed';client='ExchangeOnlineManagement';version='3.11.0';commands=@('Search-UnifiedAuditLog');ids=$ids} | ConvertTo-Json -Compress
        return
    }
    $mailboxes = @(Get-Mailbox -ResultSize Unlimited)
    if ($mailboxes.Count -ne $profile.expected_mailboxes) { throw 'Mailbox collection count differs from populated users.' }
    if (@($mailboxes | Where-Object { -not $_.ExternalDirectoryObjectId }).Count) { throw 'Mailbox directory link missing.' }
    $difference = Compare-Object @($profile.expected_user_ids | Sort-Object) @($mailboxes | ForEach-Object { [string]$_.ExternalDirectoryObjectId } | Sort-Object)
    if ($difference) { throw 'Exchange mailbox IDs do not match generated Graph users.' }
    $compiled = Get-EXOMailbox -Identity $profile.mailbox_upn
    if ([string]$compiled.ExternalDirectoryObjectId -ne $profile.mailbox_user_id) { throw 'Compiled mailbox identity did not resolve to its Graph user.' }
    [ordered]@{state='passed';client='ExchangeOnlineManagement';version='3.11.0';commands=@('Get-Mailbox','Get-EXOMailbox -Identity');mailboxes=$mailboxes.Count} | ConvertTo-Json -Compress
} finally {
    Disconnect-ExchangeOnline -Confirm:$false | Out-Null
}

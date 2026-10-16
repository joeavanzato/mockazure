$ErrorActionPreference = 'Stop'
$result = [ordered]@{runtime = $PSVersionTable.PSVersion.ToString()}
try {
    [System.Net.Dns]::GetHostAddresses('example.com') | Out-Null
    $result.dns_blocked = $false
} catch { $result.dns_blocked = $true }
foreach ($item in @(@('ipv4_blocked', '1.1.1.1'), @('ipv6_blocked', '2606:4700:4700::1111'))) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync($item[1], 443)
        $finished = $task.Wait(2000)
        $result[$item[0]] = -not ($finished -and $client.Connected)
    } catch { $result[$item[0]] = $true }
    finally { $client.Dispose() }
}
$result | ConvertTo-Json | Set-Content /state/network.json
$result | ConvertTo-Json
if (-not ($result.dns_blocked -and $result.ipv4_blocked -and $result.ipv6_blocked)) {
    throw 'Client isolation failed'
}

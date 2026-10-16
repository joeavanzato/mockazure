"""Fail qualification if a client can resolve/connect to external services."""

import json
import socket
from pathlib import Path

result = {}
try:
    socket.getaddrinfo("example.com", 443)
    result["dns_blocked"] = False
except OSError:
    result["dns_blocked"] = True
for name, address in (("ipv4_blocked", "1.1.1.1"), ("ipv6_blocked", "2606:4700:4700::1111")):
    try:
        with socket.create_connection((address, 443), timeout=2):
            result[name] = False
    except OSError:
        result[name] = True
Path("/state/network.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result))
if not all(result.values()):
    raise SystemExit("Client isolation failed")

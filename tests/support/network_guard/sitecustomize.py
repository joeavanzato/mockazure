"""Qualification-only networking guard, loaded before native Python clients."""

import ipaddress
import os
import sys


def local_host(host):
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    if host in (None, "", "localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def audit(event, args):
    host = None
    if event == "socket.getaddrinfo":
        host = args[0]
    elif event == "socket.connect" and isinstance(args[1], tuple):
        host = args[1][0]
    else:
        return
    if not local_host(host):
        log = os.environ.get("MOCKAZURE_TEST_NETWORK_LOG")
        if log:
            with open(log, "a", encoding="ascii") as stream:
                stream.write("blocked\n")
        raise PermissionError("MockAzure qualification blocks nonlocal network access")


sys.addaudithook(audit)

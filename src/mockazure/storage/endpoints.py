"""Development-style path addressing on four explicit local HTTPS origins."""

import re

from mockazure.endpoints import local_origin

SERVICES = {"blob": "storage", "file": "file", "queue": "queue", "table": "table"}


def service_endpoint(account: str, service: str, profile: dict) -> str:
    if not re.fullmatch(r"[a-z0-9]{3,24}", account) or service not in SERVICES:
        raise ValueError("Invalid Storage endpoint")
    return local_origin(profile[SERVICES[service]]) + "/" + account + "/"


def account_endpoints(account, kind, profile):
    services = (
        ("file",)
        if kind == "FileStorage"
        else ("blob",)
        if kind in {"BlobStorage", "BlockBlobStorage"}
        else SERVICES
    )
    return {service: service_endpoint(account, service, profile) for service in services}


def listener_ports(settings):
    from urllib.parse import urlsplit

    data = settings.data_listen_ports
    if data is None:
        data = tuple(
            urlsplit(getattr(settings.endpoints, name)).port or 443
            for name in ("storage", "file", "queue", "table", "keyvault")
        )
    ports = (settings.listen_port, *data)
    if len(set(ports)) != len(ports) or any(
        type(p) is not int or not 1 <= p <= 65535 for p in ports
    ):
        raise ValueError("Listener ports must be distinct valid TCP ports")
    return ports


def open_listeners(settings):
    import socket

    listeners = []
    try:
        for port in listener_ports(settings):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listeners.append(listener)
            # Do not enable SO_REUSEADDR on Windows: it permits competing bindings.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind((settings.listen_host, port))
        return listeners
    except BaseException:
        for listener in listeners:
            listener.close()
        raise

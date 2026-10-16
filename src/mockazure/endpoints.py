"""Local network origins are distinct from Microsoft resource audiences."""

from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


def local_origin(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    try:
        local = ip_address(host).is_loopback or ip_address(host).is_private
    except ValueError:
        local = host == "localhost" or (
            bool(host) and ("." not in host or host.endswith((".test", ".localhost", ".internal")))
        )
    if (
        parsed.scheme != "https"
        or not local
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or not host
        or "\\" in value
    ):
        raise ValueError("Service endpoint must be an explicit local HTTPS origin")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid service port") from None
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid service port")
    return value.rstrip("/")


class EndpointProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)
    login: str = "https://localhost:8443"
    graph: str = "https://localhost:8443"
    exchange: str = "https://localhost:8443"
    arm: str = "https://localhost:8443"
    browser_login: str = "https://localhost:8443"

    @field_validator("login", "graph", "exchange", "arm", "browser_login")
    @classmethod
    def origins(cls, value: str) -> str:
        return local_origin(value)

    def issuer(self, tenant_id: str) -> str:
        return f"{self.login}/{UUID(tenant_id)}/v2.0"


@dataclass(frozen=True)
class ResourceDescriptor:
    service: str
    audience: str
    aliases: tuple[str, ...]


RESOURCES = (
    ResourceDescriptor(
        "graph", "00000003-0000-0000-c000-000000000000", ("https://graph.microsoft.com",)
    ),
    ResourceDescriptor(
        "exchange",
        "https://outlook.office365.com",
        (
            "https://outlook.office365.com",
            "https://outlook.office.com",
            "00000002-0000-0ff1-ce00-000000000000",
        ),
    ),
    ResourceDescriptor(
        "arm",
        "https://management.azure.com/",
        ("https://management.azure.com", "https://management.core.windows.net"),
    ),
)


def resolve_resource(alias: str, profile: EndpointProfile) -> ResourceDescriptor:
    normalized = alias.rstrip("/")
    for resource in RESOURCES:
        if normalized in (resource.audience.rstrip("/"), *resource.aliases):
            return resource
    # A shared origin cannot select one of several services unambiguously.
    matches = [r for r in RESOURCES if normalized == getattr(profile, r.service)]
    if len(matches) == 1:
        return matches[0]
    raise ValueError("Unknown resource or ambiguous local resource alias")

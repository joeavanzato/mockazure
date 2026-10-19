"""Explicit local HTTPS service calls with ordinary client-credential authorization."""

import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import SecretStr

from mockazure.admin.errors import AdminError
from mockazure.endpoints import local_origin

AUDIENCES = {
    "graph": "https://graph.microsoft.com",
    "arm": "https://management.azure.com",
    "exchange": "https://outlook.office365.com",
    "activity": "https://manage.office.com",
    "storage": "https://storage.azure.com",
    "file": "https://storage.azure.com",
    "queue": "https://storage.azure.com",
    "table": "https://storage.azure.com",
    "keyvault": "https://vault.azure.net",
    "logs": "https://api.loganalytics.io",
}


@dataclass(frozen=True)
class ClientCredential:
    client_id: UUID
    secret: SecretStr


class LocalTransport:
    def __init__(self, context, credential: ClientCredential, *, client=None):
        self.context = context
        self.credential = credential
        self.client = client

    async def request(
        self, service, method, path, *, json=None, content=None, params=None, headers=None
    ):
        if service not in AUDIENCES:
            raise ValueError("Unknown local service")
        parsed = urlsplit(path)
        if (
            not path.startswith("/")
            or path.startswith("//")
            or "\\" in path
            or parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or parsed.query
            or any(ord(c) < 32 for c in path)
            or path.lower().startswith("/_mock")
        ):
            raise ValueError("Service actions require a relative public resource path")
        if any(
            name.lower() in {"authorization", "host", "cookie", "proxy-authorization"}
            for name in headers or {}
        ):
            raise ValueError("Service credentials and authority are managed by the instance")
        settings = self.context.settings
        origin = local_origin(getattr(settings.endpoints, service))
        login_origin = local_origin(settings.endpoints.login)
        owned = self.client is None
        client = self.client
        if owned:
            client = httpx.AsyncClient(
                verify=ssl.create_default_context(cafile=str(settings.ca_file)),
                trust_env=False,
                follow_redirects=False,
                timeout=60,
            )
        try:
            token = await client.post(
                f"{login_origin}/{self.context.tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": str(self.credential.client_id),
                    "client_secret": self.credential.secret.get_secret_value(),
                    "scope": AUDIENCES[service] + "/.default",
                },
                headers={"Authorization": "", "Cookie": ""},
                follow_redirects=False,
            )
            if 300 <= token.status_code < 400:
                raise AdminError(502, "UnexpectedRedirect", "Local login redirected unexpectedly")
            if token.status_code != 200:
                raise AdminError(
                    401, "LocalIdentityRejected", "Local test identity could not authenticate"
                )
            raw = token.json().get("access_token")
            if not isinstance(raw, str) or not raw:
                raise AdminError(
                    502, "InvalidTokenResponse", "Local login returned no access token"
                )
            response = await client.request(
                method,
                origin + path,
                json=json,
                content=content,
                params=params,
                headers={**(headers or {}), "Authorization": "Bearer " + raw, "Cookie": ""},
                follow_redirects=False,
            )
            if 300 <= response.status_code < 400:
                raise AdminError(502, "UnexpectedRedirect", "Local service redirected unexpectedly")
            return response
        except httpx.HTTPError:
            raise AdminError(
                503, "LocalServiceUnavailable", "Local service connection failed"
            ) from None
        finally:
            if owned:
                await client.aclose()

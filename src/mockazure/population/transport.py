"""Explicit local HTTPS service calls with ordinary client-credential authorization."""

import hashlib
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
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
        self.shared_key = False

    async def request(
        self,
        service,
        method,
        path,
        *,
        json=None,
        content=None,
        params=None,
        headers=None,
        digest=False,
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
            authorization = "Bearer " + raw
            headers = dict(headers or {})
            if self.shared_key and service == "file":
                # Namespace management does not accept FileREST OAuth. Obtain a key
                # using the selected identity's actual ARM listKeys permission.
                if json is not None or content is not None:
                    raise ValueError("Shared Key adapter is limited to empty namespace requests")
                from sqlalchemy import select

                from mockazure.storage.models import StorageAccount
                from mockazure.storage.signing import canonicalize_storage_request, signature

                account = path.split("/")[1]
                async with self.context.database.sessions() as session:
                    scope = await session.scalar(
                        select(StorageAccount.scope_key).where(
                            StorageAccount.tenant_id == self.context.tenant_id,
                            StorageAccount.name == account,
                        )
                    )
                if not scope:
                    raise AdminError(404, "AccountNotFound", "Storage account not found in tenant")
                keys = await self.request(
                    "arm", "POST", scope + "/listKeys", params={"api-version": "2025-06-01"}
                )
                if keys.status_code != 200:
                    raise AdminError(
                        keys.status_code,
                        "StorageKeyAccessDenied",
                        "Selected identity cannot list local account keys",
                    )
                headers["x-ms-date"] = format_datetime(datetime.now(UTC), usegmt=True)
                if method in {"PUT", "POST"}:
                    headers["Content-Length"] = "0"
                url = httpx.URL(origin + path, params=params)
                canonical = canonicalize_storage_request(
                    method,
                    url.raw_path.split(b"?")[0],
                    list(url.params.multi_items()),
                    list(headers.items()),
                    account,
                    "file",
                    headers["x-ms-version"],
                )
                authorization = (
                    "SharedKey "
                    + account
                    + ":"
                    + signature(keys.json()["keys"][0]["value"], canonical)
                )
            async with client.stream(
                method,
                origin + path,
                json=json,
                content=content,
                params=params,
                headers={**(headers or {}), "Authorization": authorization, "Cookie": ""},
                follow_redirects=False,
            ) as response:
                if 300 <= response.status_code < 400:
                    raise AdminError(
                        502, "UnexpectedRedirect", "Local service redirected unexpectedly"
                    )
                hashing = digest and 200 <= response.status_code < 300
                hasher, size, chunks = hashlib.sha256(), 0, []
                limit = settings.content_max_object_bytes if hashing else 16 * 1024 * 1024
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise AdminError(
                            413,
                            "ServiceResponseTooLarge",
                            "Use a bounded resource or content operation",
                        )
                    if hashing:
                        hasher.update(chunk)
                    else:
                        chunks.append(chunk)
                if hashing:
                    return httpx.Response(
                        response.status_code,
                        json={"sha256": hasher.hexdigest(), "size": size},
                        request=response.request,
                    )
                return httpx.Response(
                    response.status_code,
                    content=b"".join(chunks),
                    headers=response.headers,
                    request=response.request,
                )
        except httpx.HTTPError:
            raise AdminError(
                503, "LocalServiceUnavailable", "Local service connection failed"
            ) from None
        finally:
            if owned:
                await client.aclose()

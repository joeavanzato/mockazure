from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import JSONResponse

from mockazure.db.models import Tenant
from mockazure.identity.keys import SigningKeyStore, read_wrapping_key
from mockazure.oauth.errors import OAuthError

router = APIRouter()


async def resolve_tenant(session, name: str) -> Tenant:
    if len(name) > 320:
        raise OAuthError("invalid_request", "Local tenant not found")
    try:
        query = select(Tenant).where(Tenant.id == UUID(name))
    except ValueError:
        query = select(Tenant).where(Tenant.domain == name.lower())
    tenant = await session.scalar(query)
    if tenant is None:
        raise OAuthError("invalid_request", "Local tenant not found")
    return tenant


@router.get("/{tenant}/v2.0/.well-known/openid-configuration")
async def metadata(tenant: str, request: Request):
    profile = request.app.state.settings.endpoints
    if tenant not in ("common", "organizations"):
        async with request.app.state.database.sessions() as session:
            tenant = str((await resolve_tenant(session, tenant)).id)
    issuer_tenant = "{tenantid}" if tenant in ("common", "organizations") else tenant
    base = f"{profile.login}/{tenant}"
    return {
        "issuer": f"{profile.login}/{issuer_tenant}/v2.0",
        "authorization_endpoint": f"{base}/oauth2/v2.0/authorize",
        "token_endpoint": f"{base}/oauth2/v2.0/token",
        "device_authorization_endpoint": f"{base}/oauth2/v2.0/devicecode",
        "jwks_uri": f"{base}/discovery/v2.0/keys",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query", "form_post"],
        "subject_types_supported": ["pairwise"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "private_key_jwt"],
        "grant_types_supported": ["client_credentials"],
        "scopes_supported": ["openid", "profile", "email", "offline_access"],
    }


@router.get("/{tenant}/discovery/v2.0/keys")
async def jwks(tenant: str, request: Request):
    settings = request.app.state.settings
    async with request.app.state.database.sessions() as session:
        if tenant not in ("common", "organizations"):
            await resolve_tenant(session, tenant)
        try:
            keys = SigningKeyStore(
                session, read_wrapping_key(settings.data_dir), settings.endpoints
            )
            data = await keys.public_jwks(datetime.now(UTC))
        except ValueError:
            raise OAuthError(
                "temporarily_unavailable", "Local identity is not ready", 503
            ) from None
        return JSONResponse(data, headers={"Cache-Control": "public, max-age=300"})


@router.get("/common/discovery/instance")
async def instance(request: Request, authorization_endpoint: str = "", api_version: str = "1.1"):
    profile = request.app.state.settings.endpoints
    parsed = urlsplit(authorization_endpoint)
    expected = urlsplit(profile.login)
    parts = parsed.path.strip("/").split("/")
    if (
        parsed.scheme != expected.scheme
        or parsed.netloc != expected.netloc
        or parsed.query
        or parsed.fragment
        or len(parts) != 4
        or parts[1:] != ["oauth2", "v2.0", "authorize"]
    ):
        raise OAuthError("invalid_request", "Authority is not configured on this local instance")
    tenant = parts[0]
    if tenant not in ("common", "organizations"):
        async with request.app.state.database.sessions() as session:
            await resolve_tenant(session, tenant)
    return {
        "tenant_discovery_endpoint": (
            f"{profile.login}/{tenant}/v2.0/.well-known/openid-configuration"
        ),
        "api-version": "1.1",
        "metadata": [
            {
                "preferred_network": expected.netloc,
                "preferred_cache": expected.netloc,
                "aliases": [expected.netloc],
            }
        ],
    }

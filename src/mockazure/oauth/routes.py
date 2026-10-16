from datetime import UTC, datetime

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import JSONResponse

from mockazure.endpoints import resolve_resource
from mockazure.identity.keys import TOKEN_SECONDS, SigningKeyStore, read_wrapping_key
from mockazure.identity.models import Grant
from mockazure.identity.tokens import issue_access_token
from mockazure.identity.types import PrincipalContext
from mockazure.oauth.authorization import redeem_code
from mockazure.oauth.client_auth import authenticate_client
from mockazure.oauth.device import redeem_device_code
from mockazure.oauth.discovery import resolve_tenant
from mockazure.oauth.errors import OAuthError
from mockazure.oauth.forms import read_form
from mockazure.oauth.sessions import clock, refresh_session

router = APIRouter()


@router.post("/{tenant}/oauth2/v2.0/token")
async def token(tenant: str, request: Request):
    fields = await read_form(request)
    if fields.get("grant_type") in (
        "refresh_token",
        "urn:ietf:params:oauth:grant-type:device_code",
    ):
        handler = refresh_session if fields["grant_type"] == "refresh_token" else redeem_device_code
        async with request.app.state.database.sessions.begin() as session:
            result = await handler(fields, tenant, request, session, clock(request))
        if isinstance(result, OAuthError):
            raise result
        return JSONResponse(result, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    if fields.get("grant_type") == "authorization_code":
        async with request.app.state.database.sessions.begin() as session:
            result = await redeem_code(fields, tenant, request, session, clock(request))
        return JSONResponse(result, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    if fields.get("grant_type") != "client_credentials":
        raise OAuthError("unsupported_grant_type", "This authorization grant is not supported")
    requested = fields.get("scope", "").split()
    if len(requested) != 1 or not requested[0].endswith("/.default"):
        raise OAuthError("invalid_scope", "Client credentials require one resource/.default scope")
    settings = request.app.state.settings
    try:
        resource = resolve_resource(requested[0][: -len("/.default")], settings.endpoints)
    except ValueError:
        raise OAuthError("invalid_scope", "Resource audience is not configured") from None
    now = datetime.now(UTC)
    async with request.app.state.database.sessions.begin() as session:
        tenant_row = await resolve_tenant(session, tenant)
        app, sp = await authenticate_client(
            session,
            tenant_row.id,
            fields,
            f"{settings.endpoints.login}/{tenant}/oauth2/v2.0/token",
            now,
        )
        grants = await session.scalars(
            select(Grant).where(
                Grant.tenant_id == tenant_row.id,
                Grant.client_id == app.client_id,
                Grant.resource == resource.service,
                Grant.kind == "application",
            )
        )
        permissions = frozenset(p for grant in grants for p in grant.permissions)
        context = PrincipalContext(
            tenant_row.id, app.client_id, sp.id, "application", resource.audience, permissions
        )
        try:
            keys = SigningKeyStore(
                session, read_wrapping_key(settings.data_dir), settings.endpoints
            )
            access_token = await issue_access_token(context, now, keys)
        except ValueError:
            raise OAuthError(
                "temporarily_unavailable", "Local identity is not ready", 503
            ) from None
        return JSONResponse(
            {"token_type": "Bearer", "expires_in": TOKEN_SECONDS, "access_token": access_token},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

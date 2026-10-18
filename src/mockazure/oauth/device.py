import secrets
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import JSONResponse

from mockazure.identity.models import Application
from mockazure.oauth import pages
from mockazure.oauth.authorization import COOKIE, current_user, delegated_client, local_app
from mockazure.oauth.device_models import DeviceAuthorization
from mockazure.oauth.discovery import resolve_tenant
from mockazure.oauth.errors import OAuthError
from mockazure.oauth.forms import read_form
from mockazure.oauth.models import AuthorizationTransaction
from mockazure.oauth.scopes import parse_scopes, require_grants
from mockazure.oauth.sessions import clock, digest, new_session, record_outcome, token_response

router = APIRouter()


def normalized_code(raw):
    return raw.replace("-", "").replace(" ", "").upper()


@router.post("/{tenant}/oauth2/v2.0/devicecode")
async def start(tenant: str, request: Request):
    fields, now = await read_form(request), clock(request)
    settings = request.app.state.settings
    async with request.app.state.database.sessions.begin() as session:
        if tenant in ("common", "organizations"):
            try:
                app = await session.get(Application, UUID(fields.get("client_id", "")))
            except ValueError:
                app = None
            if not app:
                raise OAuthError("invalid_client", "Local application is unavailable", 401)
            tenant_id = app.tenant_id
        else:
            tenant_id = (await resolve_tenant(session, tenant)).id
        app = await local_app(session, tenant_id, fields.get("client_id", ""))
        resource, scopes = parse_scopes(fields.get("scope", ""), app, settings.endpoints)
        raw = secrets.token_urlsafe(48)
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(9))
        display = code[:5] + "-" + code[5:]
        session.add(
            DeviceAuthorization(
                tenant_id=tenant_id,
                client_id=app.client_id,
                common=tenant in ("common", "organizations"),
                digest=digest(raw),
                user_code_hash=digest(code),
                resource=resource,
                scopes=scopes,
                status="pending",
                expires_at=now + timedelta(seconds=900),
                interval=5,
            )
        )
        uri = settings.endpoints.browser_login + "/device"
        return JSONResponse(
            {
                "device_code": raw,
                "user_code": display,
                "verification_uri": uri,
                "expires_in": 900,
                "interval": 5,
                "message": f"To sign in locally, open {uri} and enter code {display}.",
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )


@router.get("/device")
async def device_page(request: Request, user_code: str = ""):
    if not user_code:
        return pages.page(
            "Device sign-in",
            '<form action="/device" method="get">'
            '<label>Device code <input name="user_code" required></label>'
            "<button>Continue</button></form>",
        )
    now = clock(request)
    async with request.app.state.database.sessions.begin() as session:
        device = await session.scalar(
            select(DeviceAuthorization).where(
                DeviceAuthorization.user_code_hash == digest(normalized_code(user_code))
            )
        )
        if not device or device.expires_at <= now or device.status != "pending":
            raise OAuthError("invalid_request", "Device code is invalid or expired")
        app = await local_app(session, device.tenant_id, str(device.client_id))
        cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        row = AuthorizationTransaction(
            tenant_id=device.tenant_id,
            client_id=device.client_id,
            common=device.common,
            redirect_uri="",
            scopes=device.scopes,
            resource=device.resource,
            state="",
            nonce="",
            challenge="",
            response_mode="query",
            cookie_hash=digest(cookie),
            csrf_hash=digest(csrf),
            stage="login",
            expires_at=device.expires_at,
            device_id=device.id,
        )
        session.add(row)
        await session.flush()
        response = pages.form(row, csrf, "login", app.display_name)
        response.set_cookie(COOKIE, cookie, max_age=900, secure=True, httponly=True, samesite="lax")
        return response


async def complete_device(row, session, now, *, approved, request):
    device = await session.scalar(
        select(DeviceAuthorization).where(DeviceAuthorization.id == row.device_id).with_for_update()
    )
    if not device or device.status != "pending" or device.expires_at <= now:
        raise OAuthError("invalid_request", "Device authorization is no longer pending")
    device.credential_version = row.credential_version
    device.authenticated_at = row.authenticated_at
    device.status = "approved" if approved else "denied"
    device.tenant_id, device.user_id, device.mfa = row.tenant_id, row.user_id, row.mfa
    await record_outcome(
        session,
        row,
        "device_approval",
        now,
        "success" if approved else "access_denied",
        request=request,
    )
    return pages.page(
        "Device sign-in",
        "<p>"
        + (
            "Approved. Return to your application."
            if approved
            else "Denied. The application will not receive a token."
        )
        + "</p>",
    )


async def redeem_device_code(fields, tenant, request, session, now):
    row = await session.scalar(
        select(DeviceAuthorization)
        .where(DeviceAuthorization.digest == digest(fields.get("device_code", "")))
        .with_for_update()
    )
    if not row or str(row.client_id) != fields.get("client_id") or row.status == "consumed":
        return OAuthError("invalid_grant", "Device authorization is invalid")
    if tenant not in ("common", "organizations"):
        if (await resolve_tenant(session, tenant)).id != row.tenant_id:
            return OAuthError("invalid_grant", "Device authorization is invalid")
    await delegated_client(session, row.tenant_id, fields, request, now)
    if row.expires_at <= now:
        return OAuthError("expired_token", "Device authorization expired")
    if row.status == "denied":
        return OAuthError("authorization_declined", "The local user denied authorization")
    if row.last_poll and (now - row.last_poll).total_seconds() < row.interval:
        row.interval += 5
        row.last_poll = now
        return OAuthError("slow_down", "Increase the polling interval by five seconds")
    row.last_poll = now
    if row.status == "pending":
        return OAuthError("authorization_pending", "Local user approval is pending")
    user = await current_user(row, session, request=request, flow="device_code", now=now)
    await require_grants(session, row)
    from mockazure.security.evaluation import enforce_policies

    await enforce_policies(session, request, row, user, "device_code", now)
    login = await new_session(row, session, now)
    result = await token_response(login, user, session, request.app.state.settings, now)
    row.status = "consumed"
    await record_outcome(session, row, "device_code", now, "success", request=request)
    return result

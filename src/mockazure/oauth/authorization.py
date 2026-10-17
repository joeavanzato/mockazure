import asyncio
import base64
import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from sqlalchemy import select

from mockazure.authorization.catalog import permission
from mockazure.identity.credentials import verify_secret
from mockazure.identity.models import Application, DirectoryRole, Grant, ServicePrincipal, User
from mockazure.oauth import pages
from mockazure.oauth.client_auth import authenticate_client
from mockazure.oauth.discovery import resolve_tenant
from mockazure.oauth.errors import OAuthError
from mockazure.oauth.forms import read_form
from mockazure.oauth.models import AuthorizationTransaction
from mockazure.oauth.scopes import OIDC, granted_scopes, parse_scopes, require_grants
from mockazure.oauth.sessions import clock, digest, new_session, record_outcome, token_response

router = APIRouter()
COOKIE = "__Host-mockazure-flow"


def invalid_grant():
    return OAuthError("invalid_grant", "Authorization is invalid, expired or no longer permitted")


async def local_app(session, tenant_id, client_id):
    try:
        app = await session.get(Application, UUID(client_id))
    except (ValueError, TypeError):
        app = None
    sp = (
        await session.scalar(
            select(ServicePrincipal).where(
                ServicePrincipal.tenant_id == tenant_id, ServicePrincipal.client_id == app.client_id
            )
        )
        if app
        else None
    )
    if not app or not app.enabled or not sp or not sp.enabled:
        raise OAuthError("invalid_client", "Local application is unavailable", 401)
    return app


async def delegated_client(session, tenant_id, fields, request, now):
    app = await local_app(session, tenant_id, fields.get("client_id", ""))
    if app.client_mode == "confidential":
        profile = request.app.state.settings.advertised_endpoints(request.headers.get("host", ""))
        await authenticate_client(
            session,
            tenant_id,
            fields,
            f"{profile.login}/{request.path_params['tenant']}/oauth2/v2.0/token",
            now,
            request=request,
        )
    elif fields.get("client_secret") or fields.get("client_assertion"):
        raise OAuthError("invalid_client", "Public clients must not send credentials", 401)
    return app


async def begin_authorization(fields, tenant, request, session, now):
    settings = request.app.state.settings
    if len(str(fields)) > 16384 or len(fields) > 32:
        raise OAuthError("invalid_request", "Authorization request is too large")
    if tenant in ("common", "organizations"):
        try:
            app = await session.get(Application, UUID(fields.get("client_id", "")))
        except ValueError:
            app = None
        if app is None:
            raise OAuthError("invalid_client", "Local application is unavailable")
        tenant_id = app.tenant_id
    else:
        tenant_id = (await resolve_tenant(session, tenant)).id
    app = await local_app(session, tenant_id, fields.get("client_id", ""))
    redirect = fields.get("redirect_uri", "")
    if redirect not in app.redirect_uris:
        raise OAuthError("invalid_request", "Redirect URI is not registered")
    if fields.get("response_type") != "code":
        raise OAuthError("unsupported_response_type", "Only authorization code is supported")
    challenge = fields.get("code_challenge", "")
    if fields.get("code_challenge_method") != "S256" or not re.fullmatch(
        r"[A-Za-z0-9_-]{43}", challenge
    ):
        raise OAuthError("invalid_request", "S256 PKCE is required")
    mode = fields.get("response_mode", "query")
    if mode not in ("query", "form_post"):
        raise OAuthError("invalid_request", "Unsupported response mode")
    if any(len(fields.get(name, "")) > 2048 for name in ("state", "nonce")):
        raise OAuthError("invalid_request", "Authorization parameter exceeds limit")
    if fields.get("prompt") == "none":
        raise OAuthError("login_required", "Interactive local sign-in is required")
    resource, scopes = parse_scopes(fields.get("scope", ""), app, settings.endpoints)
    cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    transaction = AuthorizationTransaction(
        tenant_id=tenant_id,
        client_id=app.client_id,
        common=tenant in ("common", "organizations"),
        redirect_uri=redirect,
        scopes=scopes,
        resource=resource,
        state=fields.get("state", ""),
        nonce=fields.get("nonce", ""),
        challenge=challenge,
        response_mode=mode,
        cookie_hash=digest(cookie),
        csrf_hash=digest(csrf),
        stage="login",
        expires_at=now + timedelta(minutes=15),
    )
    session.add(transaction)
    await session.flush()
    response = pages.form(transaction, csrf, "login", app.display_name)
    response.set_cookie(COOKIE, cookie, max_age=900, secure=True, httponly=True, samesite="lax")
    return response


@router.get("/{tenant}/oauth2/v2.0/authorize")
async def authorize(tenant: str, request: Request):
    pairs = list(request.query_params.multi_items())
    if len({k for k, _ in pairs}) != len(pairs):
        raise OAuthError("invalid_request", "Repeated authorization parameter")
    async with request.app.state.database.sessions.begin() as session:
        return await begin_authorization(dict(pairs), tenant, request, session, clock(request))


async def browser_transaction(request, fields, session, stage, now):
    try:
        transaction_id = UUID(fields.get("transaction", ""))
    except ValueError:
        raise OAuthError("invalid_request", "Invalid browser transaction") from None
    row = await session.scalar(
        select(AuthorizationTransaction)
        .where(AuthorizationTransaction.id == transaction_id)
        .with_for_update()
    )
    if (
        not row
        or row.expires_at <= now
        or row.stage != stage
        or not hmac.compare_digest(row.cookie_hash, digest(request.cookies.get(COOKIE, "")))
        or not hmac.compare_digest(row.csrf_hash, digest(fields.get("csrf", "")))
    ):
        raise OAuthError("invalid_request", "Invalid browser transaction or CSRF token")
    app = await local_app(session, row.tenant_id, str(row.client_id))
    if not row.device_id and row.redirect_uri not in app.redirect_uris:
        raise OAuthError("invalid_request", "Callback registration changed")
    return row, app


@router.post("/login")
async def login(request: Request):
    fields, now = await read_form(request), clock(request)
    async with request.app.state.database.sessions.begin() as session:
        row, app = await browser_transaction(request, fields, session, "login", now)
        username = fields.get("username", "").strip().lower()
        if not username or len(username) > 320:
            raise OAuthError("invalid_request", "Username exceeds the supported bounds")
        query = select(User).where(User.upn == username)
        if not row.common:
            query = query.where(User.tenant_id == row.tenant_id)
        user = await session.scalar(query)
        if (
            not user
            or not user.enabled
            or user.deleted_at is not None
            or not await asyncio.to_thread(verify_secret, fields.get("password", ""), user.verifier)
        ):
            from mockazure.evidence.authentication import annotate_failure

            error = OAuthError("access_denied", "Local username or password is invalid")
            target_tenant = user.tenant_id if user else row.tenant_id
            binding = await session.scalar(
                select(ServicePrincipal).where(
                    ServicePrincipal.tenant_id == target_tenant,
                    ServicePrincipal.client_id == app.client_id,
                    ServicePrincipal.enabled.is_(True),
                )
            )
            if binding is not None:
                code = (
                    50126
                    if user is None
                    else (
                        50034
                        if user.deleted_at is not None
                        else (50057 if not user.enabled else 50126)
                    )
                )
                error = await annotate_failure(
                    error,
                    session,
                    request,
                    target_tenant,
                    app.client_id,
                    user.id if user else None,
                    "device_code" if row.device_id else "authorization_code",
                    now,
                    error_code=code,
                    resource=row.resource,
                    attempted_upn=username,
                )
            raise error
        await local_app(session, user.tenant_id, str(app.client_id))
        row.tenant_id, row.user_id, row.stage = user.tenant_id, user.id, "consent"
        return pages.form(row, fields["csrf"], "consent", app.display_name)


async def consent_to_scopes(row, session):
    from mockazure.security.repository import lock_security

    await lock_security(session, row.tenant_id)
    granted = await granted_scopes(session, row.tenant_id, row.client_id, row.user_id, row.resource)
    needed = set(row.scopes) - OIDC - granted
    admin = await session.get(DirectoryRole, (row.tenant_id, row.user_id, "Global Administrator"))
    if (
        any(permission(row.resource, "delegated", p).admin_consent_required for p in needed)
        and not admin
    ):
        raise OAuthError("consent_required", "A local administrator must consent to these scopes")
    if needed:
        # Serialize simultaneous consent for a user before creating its unique grant.
        await session.scalar(select(User).where(User.id == row.user_id).with_for_update())
        grant = await session.scalar(
            select(Grant).where(
                Grant.tenant_id == row.tenant_id,
                Grant.client_id == row.client_id,
                Grant.user_id == row.user_id,
                Grant.resource == row.resource,
                Grant.kind == "delegated",
            )
        )
        if grant:
            grant.permissions = sorted(set(grant.permissions) | needed)
        else:
            session.add(
                Grant(
                    id=uuid4(),
                    tenant_id=row.tenant_id,
                    client_id=row.client_id,
                    user_id=row.user_id,
                    resource=row.resource,
                    kind="delegated",
                    permissions=sorted(needed),
                )
            )
        await session.flush()


async def current_user(row, session, *, request=None, flow="authorization_code", now=None):
    user = await session.get(User, row.user_id)
    if (
        not user
        or not user.enabled
        or user.deleted_at is not None
        or user.tenant_id != row.tenant_id
    ):
        error = invalid_grant()
        if (
            request is not None
            and row.user_id
            and (user is None or user.tenant_id == row.tenant_id)
        ):
            from mockazure.evidence.authentication import annotate_failure

            error = await annotate_failure(
                error,
                session,
                request,
                row.tenant_id,
                row.client_id,
                row.user_id,
                flow,
                now,
                error_code=50034 if user is None or user.deleted_at else 50057,
                resource=row.resource,
                mfa=row.mfa,
            )
        raise error
    return user


@router.post("/consent")
async def consent(request: Request):
    fields, now = await read_form(request), clock(request)
    async with request.app.state.database.sessions.begin() as session:
        row, app = await browser_transaction(request, fields, session, "consent", now)
        await current_user(row, session, request=request, now=now)
        if fields.get("decision") == "deny":
            row.stage = "denied"
            if row.device_id:
                from mockazure.oauth.device import complete_device

                return await complete_device(row, session, now, approved=False, request=request)
            await record_outcome(
                session, row, "authorization_code", now, "access_denied", request=request
            )
            return pages.callback(row, {"error": "access_denied"})
        if fields.get("decision") != "approve":
            raise OAuthError("invalid_request", "Choose allow or deny")
        await consent_to_scopes(row, session)
        row.stage = "mfa"
        return pages.form(row, fields["csrf"], "mfa", app.display_name)


@router.post("/mfa")
async def mfa(request: Request):
    fields, now = await read_form(request), clock(request)
    async with request.app.state.database.sessions.begin() as session:
        row, _ = await browser_transaction(request, fields, session, "mfa", now)
        await current_user(row, session, request=request, now=now)
        decision = fields.get("decision")
        if decision == "deny":
            row.stage, row.mfa = "denied", "denied"
            if row.device_id:
                from mockazure.oauth.device import complete_device

                return await complete_device(row, session, now, approved=False, request=request)
            await record_outcome(
                session, row, "authorization_code", now, "access_denied", request=request
            )
            return pages.callback(row, {"error": "access_denied"})
        if decision not in ("complete", "skip"):
            raise OAuthError("invalid_request", "Choose a simulated MFA outcome")
        row.mfa = "completed" if decision == "complete" else "not_performed"
        row.stage = "authorized"
        if row.device_id:
            from mockazure.oauth.device import complete_device

            return await complete_device(row, session, now, approved=True, request=request)
        raw = secrets.token_urlsafe(48)
        row.code_hash, row.code_expires_at = digest(raw), now + timedelta(seconds=300)
        return pages.callback(row, {"code": raw})


async def redeem_code(fields, tenant, request, session, now):
    row = await session.scalar(
        select(AuthorizationTransaction)
        .where(AuthorizationTransaction.code_hash == digest(fields.get("code", "")))
        .with_for_update()
    )
    if not row or row.stage != "authorized" or row.consumed_at or row.code_expires_at <= now:
        raise invalid_grant()
    if tenant not in ("common", "organizations"):
        if (await resolve_tenant(session, tenant)).id != row.tenant_id:
            raise invalid_grant()
    app = await delegated_client(session, row.tenant_id, fields, request, now)
    verifier = fields.get("code_verifier", "")
    if (
        app.client_id != row.client_id
        or fields.get("redirect_uri") != row.redirect_uri
        or row.redirect_uri not in app.redirect_uris
        or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
    ):
        raise invalid_grant()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    if not hmac.compare_digest(row.challenge, challenge):
        raise invalid_grant()
    user = await current_user(row, session, request=request, now=now)
    await require_grants(session, row)
    row.consumed_at = now
    await record_outcome(session, row, "authorization_code", now, "success", request=request)
    login_session = await new_session(row, session, now)
    return await token_response(
        login_session, user, session, request.app.state.settings, now, nonce=row.nonce
    )

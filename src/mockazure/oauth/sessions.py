import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import jwt

from mockazure.endpoints import RESOURCES
from mockazure.identity.keys import TOKEN_SECONDS, SigningKeyStore, read_wrapping_key
from mockazure.identity.tokens import issue_access_token
from mockazure.identity.types import PrincipalContext
from mockazure.oauth.models import LoginSession, RefreshHandle
from mockazure.oauth.scopes import OIDC


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def clock(request):
    return getattr(request.app.state, "clock", lambda: datetime.now(UTC))()


async def new_session(transaction, session, now):
    from mockazure.security.evaluation import client_category

    result = LoginSession(
        client_app_type=await client_category(
            session,
            transaction,
            "device_code" if hasattr(transaction, "user_code_hash") else "authorization_code",
        ),
        tenant_id=transaction.tenant_id,
        client_id=transaction.client_id,
        user_id=transaction.user_id,
        resource=transaction.resource,
        scopes=transaction.scopes,
        mfa=transaction.mfa,
        credential_version=transaction.credential_version,
        authenticated_at=transaction.authenticated_at,
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )
    session.add(result)
    await session.flush()
    from mockazure.security.authentication_context import copy_context

    await copy_context(session, transaction, result, now)
    return result


async def token_response(login, user, session, settings, now, *, nonce="", scopes=None):
    scopes = set(scopes if scopes is not None else login.scopes)
    keys = SigningKeyStore(session, read_wrapping_key(settings.data_dir), settings.endpoints)
    resource = next(r for r in RESOURCES if r.service == login.resource)
    context = PrincipalContext(
        login.tenant_id,
        login.client_id,
        user.id,
        "delegated",
        resource.audience,
        frozenset(scopes - OIDC),
        login.id,
    )
    result = {
        "token_type": "Bearer",
        "expires_in": TOKEN_SECONDS,
        "access_token": await issue_access_token(context, now, keys),
        "scope": " ".join(sorted(scopes)),
    }
    if "openid" in scopes:
        key = await keys.active_key(now)
        claims = {
            "iss": settings.endpoints.issuer(str(login.tenant_id)),
            "aud": str(login.client_id),
            "sub": str(user.id),
            "oid": str(user.id),
            "tid": str(login.tenant_id),
            "sid": str(login.id),
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int(now.timestamp()) + TOKEN_SECONDS,
            "amr": ["pwd", "mock_mfa"] if login.mfa == "completed" else ["pwd"],
        }
        if login.authenticated_at is not None:
            claims["auth_time"] = int(login.authenticated_at.timestamp())
        if nonce:
            claims["nonce"] = nonce
        if "profile" in scopes:
            claims.update(name=user.display_name, preferred_username=user.upn)
        if "email" in scopes:
            claims["email"] = user.upn
        result["id_token"] = jwt.encode(
            claims, keys.private_key(key), algorithm="RS256", headers={"kid": key.kid, "typ": "JWT"}
        )
    if "offline_access" in scopes:
        raw = secrets.token_urlsafe(48)
        session.add(
            RefreshHandle(
                digest=digest(raw),
                tenant_id=login.tenant_id,
                session_id=login.id,
                expires_at=login.expires_at,
            )
        )
        result["refresh_token"] = raw
    return result


async def record_outcome(session, identity, flow, now, result, *, request):
    from uuid import UUID, uuid5

    from mockazure.evidence.authentication import make_fact, record_authentication
    from mockazure.oauth.device_models import AuthenticationOutcome

    attempt_id = (
        UUID(request.state.request_id)
        if flow == "refresh_token"
        else uuid5(identity.id, flow + ":" + result)
    )
    mfa = getattr(identity, "mfa", "not_performed")
    fact = await make_fact(
        session,
        request,
        identity.tenant_id,
        identity.client_id,
        getattr(identity, "user_id", None),
        flow,
        now,
        resource=getattr(identity, "resource", None),
        mfa=mfa,
        attempt_id=attempt_id,
        error_code=0 if result == "success" else (50074 if mfa == "denied" else 65004),
    )
    session.add(
        AuthenticationOutcome(
            id=attempt_id,
            tenant_id=identity.tenant_id,
            client_id=identity.client_id,
            user_id=getattr(identity, "user_id", None),
            flow=flow,
            timestamp=now,
            correlation_id=fact.correlation_id,
            result=result,
            mfa=mfa,
        )
    )
    await record_authentication(session, fact)


async def refresh_session(fields, tenant, request, session, now):
    from sqlalchemy import select

    from mockazure.oauth.authorization import current_user, delegated_client, invalid_grant
    from mockazure.oauth.discovery import resolve_tenant
    from mockazure.oauth.errors import OAuthError
    from mockazure.oauth.scopes import parse_scopes, require_grants

    handle = await session.get(RefreshHandle, digest(fields.get("refresh_token", "")))
    if not handle or handle.expires_at <= now:
        raise invalid_grant()
    row = await session.scalar(
        select(LoginSession).where(LoginSession.id == handle.session_id).with_for_update()
    )
    if (
        not row
        or row.revoked_at
        or row.expires_at <= now
        or str(row.client_id) != fields.get("client_id")
    ):
        raise invalid_grant()
    if tenant not in ("common", "organizations"):
        if (await resolve_tenant(session, tenant)).id != row.tenant_id:
            raise invalid_grant()
    app = await delegated_client(session, row.tenant_id, fields, request, now)
    scopes = row.scopes
    if fields.get("scope"):
        resource, scopes = parse_scopes(fields["scope"], app, request.app.state.settings.endpoints)
        if resource != row.resource or not set(scopes) <= set(row.scopes):
            raise OAuthError("invalid_scope", "Refresh cannot add resources or scopes")
    user = await current_user(row, session, request=request, flow="refresh_token", now=now)
    await require_grants(session, row)
    from mockazure.security.evaluation import enforce_policies

    await enforce_policies(session, request, row, user, "refresh_token", now)
    await record_outcome(session, row, "refresh_token", now, "success", request=request)
    return await token_response(row, user, session, request.app.state.settings, now, scopes=scopes)


async def cleanup_expired(session, tenant_id, now, limit=500):
    """Bounded tenant-scoped cleanup, ordered children before parents."""
    from sqlalchemy import delete, or_, select

    from mockazure.oauth.device_models import DeviceAuthorization
    from mockazure.oauth.models import AuthorizationTransaction

    if not 1 <= limit <= 1000:
        raise ValueError("Cleanup limit must be between 1 and 1000")
    from mockazure.security.authentication_models import AuthenticationContext

    expired_contexts = (
        select(AuthenticationContext.session_id)
        .where(
            AuthenticationContext.tenant_id == tenant_id, AuthenticationContext.expires_at <= now
        )
        .order_by(AuthenticationContext.expires_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    await session.execute(
        delete(AuthenticationContext).where(AuthenticationContext.session_id.in_(expired_contexts))
    )
    for model, key in (
        (RefreshHandle, RefreshHandle.digest),
        (AuthorizationTransaction, AuthorizationTransaction.id),
    ):
        expired = (
            select(key)
            .where(model.tenant_id == tenant_id, model.expires_at <= now)
            .order_by(model.expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        if model is AuthorizationTransaction:
            expired = expired.where(
                or_(
                    AuthorizationTransaction.code_expires_at.is_(None),
                    AuthorizationTransaction.code_expires_at <= now,
                )
            )
        await session.execute(delete(model).where(key.in_(expired)))
    live_browser = select(AuthorizationTransaction.device_id).where(
        AuthorizationTransaction.device_id.is_not(None)
    )
    expired_devices = (
        select(DeviceAuthorization.id)
        .where(
            DeviceAuthorization.tenant_id == tenant_id,
            DeviceAuthorization.expires_at <= now,
            DeviceAuthorization.id.not_in(live_browser),
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    await session.execute(
        delete(DeviceAuthorization).where(DeviceAuthorization.id.in_(expired_devices))
    )
    live_handles = select(RefreshHandle.session_id)
    expired_sessions = (
        select(LoginSession.id)
        .where(
            LoginSession.tenant_id == tenant_id,
            LoginSession.expires_at <= now,
            LoginSession.id.not_in(live_handles),
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    await session.execute(delete(LoginSession).where(LoginSession.id.in_(expired_sessions)))

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
    result = LoginSession(
        tenant_id=transaction.tenant_id,
        client_id=transaction.client_id,
        user_id=transaction.user_id,
        resource=transaction.resource,
        scopes=transaction.scopes,
        mfa=transaction.mfa,
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )
    session.add(result)
    await session.flush()
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
            "auth_time": int(login.created_at.timestamp()),
            "amr": ["pwd", "mock_mfa"] if login.mfa == "completed" else ["pwd"],
        }
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

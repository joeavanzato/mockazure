"""Server-maintained simulated authentication evidence, never bearer/header assertions."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, select

from mockazure.oauth.errors import OAuthError
from mockazure.security.authentication_models import AuthenticationContext
from mockazure.security.method_policies import eligible_mfa_methods
from mockazure.security.models import SecurityObject

PLATFORMS = {"android", "iOS", "windows", "windowsPhone", "macOS", "linux"}


@dataclass(frozen=True)
class TrustedAuthenticationContext:
    session_id: UUID
    device_id: UUID | None
    platform: str | None
    method_id: str | None
    method_completed_at: datetime | None


async def load_authentication_context(session, session_id: UUID) -> TrustedAuthenticationContext:
    row = await session.get(AuthenticationContext, session_id, populate_existing=True)
    return TrustedAuthenticationContext(
        session_id,
        row.device_id if row else None,
        row.platform if row else None,
        row.method_id if row else None,
        row.method_completed_at if row else None,
    )


async def bound_context(session, identity, now):
    row = await session.get(AuthenticationContext, identity.id, populate_existing=True)
    if (
        row is None
        or row.tenant_id != identity.tenant_id
        or row.client_id != identity.client_id
        or row.expires_at <= now
    ):
        return None
    if row.user_id is not None and row.user_id != identity.user_id:
        return None
    return row


async def ensure_context(session, identity, now):
    row = await bound_context(session, identity, now)
    if row is None:
        old = await session.get(AuthenticationContext, identity.id)
        if old is not None:
            await session.delete(old)
            await session.flush()
        row = AuthenticationContext(
            session_id=identity.id,
            tenant_id=identity.tenant_id,
            client_id=identity.client_id,
            user_id=identity.user_id,
            expires_at=identity.expires_at,
        )
        session.add(row)
        await session.flush()
    return row


async def copy_context(session, source, target, now):
    row = await bound_context(session, source, now)
    if row is None:
        return
    result = await ensure_context(session, target, now)
    for name in ("device_id", "platform", "method_id", "method_version", "method_completed_at"):
        setattr(result, name, getattr(row, name))
    result.user_id = target.user_id
    await session.flush()


async def complete_method(session, identity, method_id, now):
    if not isinstance(method_id, str) or method_id not in await eligible_mfa_methods(
        session, identity.tenant_id, identity.user_id
    ):
        raise OAuthError(
            "access_denied", "Select a currently enrolled and enabled local MFA method"
        )
    method = await session.get(
        SecurityObject,
        (identity.tenant_id, uuid5(identity.user_id, "authenticationMethod:" + method_id)),
        populate_existing=True,
    )
    if not method.properties.get("registrationId"):
        method.properties = method.properties | {"registrationId": str(uuid4())}
    row = await ensure_context(session, identity, now)
    row.user_id, row.method_id = identity.user_id, method_id
    row.method_version = method.properties["registrationId"]
    row.method_completed_at = now
    await session.flush()


async def valid_method_outcome(session, identity, now):
    row = await bound_context(session, identity, now)
    if (
        not row
        or not row.method_id
        or row.user_id != identity.user_id
        or not row.method_completed_at
        or row.method_completed_at > now
    ):
        return False
    if row.method_id not in await eligible_mfa_methods(
        session, identity.tenant_id, identity.user_id
    ):
        return False
    method = await session.get(
        SecurityObject,
        (identity.tenant_id, uuid5(identity.user_id, "authenticationMethod:" + row.method_id)),
        populate_existing=True,
    )
    return bool(
        method
        and row.method_version
        and method.properties.get("registrationId") == row.method_version
    )


async def existing_device(session, tenant, device_id):
    rows = list(
        await session.scalars(
            select(SecurityObject)
            .where(
                SecurityObject.tenant_id == tenant,
                SecurityObject.kind == "device",
                func.lower(SecurityObject.properties["deviceId"].as_string()) == str(device_id),
            )
            .limit(2)
        )
    )
    return rows[0] if len(rows) == 1 and rows[0].properties.get("accountEnabled") is True else None

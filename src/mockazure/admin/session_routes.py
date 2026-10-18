from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from mockazure.admin.identity_routes import transaction
from mockazure.identity.repository import IdentityError
from mockazure.oauth.models import LoginSession
from mockazure.oauth.sessions import clock

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/identity/sessions")


@router.get("")
async def sessions(
    tenant_id: UUID,
    request: Request,
    after: UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    async with transaction(request, tenant_id) as repo:
        query = (
            select(LoginSession)
            .where(LoginSession.tenant_id == tenant_id)
            .order_by(LoginSession.id)
            .limit(limit + 1)
        )
        if after:
            query = query.where(LoginSession.id > after)
        rows = list(await repo.session.scalars(query))
        return {
            "items": [
                {
                    "id": row.id,
                    "client_id": row.client_id,
                    "user_id": row.user_id,
                    "created_at": row.created_at,
                    "expires_at": row.expires_at,
                    "revoked_at": row.revoked_at,
                    "resource": row.resource,
                    "mfa": row.mfa,
                }
                for row in rows[:limit]
            ],
            "next_after": rows[limit - 1].id if len(rows) > limit else None,
        }


@router.delete("/{session_id}")
async def revoke(tenant_id: UUID, session_id: UUID, request: Request):
    async with transaction(request, tenant_id) as repo:
        row = await repo.session.scalar(
            select(LoginSession)
            .where(LoginSession.tenant_id == tenant_id, LoginSession.id == session_id)
            .with_for_update()
        )
        if row is None:
            raise IdentityError(404, "NotFound", "Session not found")
        row.revoked_at = row.revoked_at or clock(request)
    return {"revoked": True}


@router.put("/{session_id}/authentication-context")
async def bind_context(tenant_id: UUID, session_id: UUID, request: Request):
    from pydantic import BaseModel, ConfigDict, ValidationError

    from mockazure.oauth.device_models import DeviceAuthorization
    from mockazure.oauth.models import AuthorizationTransaction
    from mockazure.providers.graph.body import read_json
    from mockazure.security.authentication_context import PLATFORMS, ensure_context, existing_device

    class Binding(BaseModel):
        model_config = ConfigDict(extra="forbid")
        device_id: UUID
        platform: str

    try:
        body = Binding.model_validate(await read_json(request, limit=4096))
    except ValidationError:
        raise IdentityError(
            422, "InvalidContext", "An existing device ID and supported platform are required"
        ) from None
    if body.platform not in PLATFORMS:
        raise IdentityError(422, "InvalidContext", "Unsupported local platform")
    async with transaction(request, tenant_id) as repo:
        now = clock(request)
        identity = None
        for model in (AuthorizationTransaction, DeviceAuthorization, LoginSession):
            candidate = await repo.session.get(model, session_id, populate_existing=True)
            if (
                candidate
                and candidate.tenant_id == tenant_id
                and candidate.expires_at > now
                and not getattr(candidate, "revoked_at", None)
            ):
                identity = candidate
                break
        if identity is None:
            raise IdentityError(
                404, "NotFound", "Current local authentication flow or session not found"
            )
        if await existing_device(repo.session, tenant_id, body.device_id) is None:
            raise IdentityError(404, "NotFound", "Current enabled local directory device not found")
        row = await ensure_context(repo.session, identity, now)
        row.device_id, row.platform = body.device_id, body.platform
        return {"session_id": session_id, "device_id": body.device_id, "platform": body.platform}


@router.get("/{session_id}/authentication-context")
async def read_context(tenant_id: UUID, session_id: UUID, request: Request):
    from mockazure.security.authentication_models import AuthenticationContext

    async with transaction(request, tenant_id) as repo:
        row = await repo.session.get(AuthenticationContext, session_id)
        if row is None or row.tenant_id != tenant_id or row.expires_at <= clock(request):
            raise IdentityError(404, "NotFound", "Current authentication context not found")
        return {
            "session_id": session_id,
            "device_id": row.device_id,
            "platform": row.platform,
            "method_id": row.method_id,
            "method_completed_at": row.method_completed_at,
        }

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

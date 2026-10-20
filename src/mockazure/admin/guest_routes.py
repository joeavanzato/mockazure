"""Authenticated local guest controls; these are not Microsoft Graph endpoints."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request

from mockazure.admin.errors import AdminError
from mockazure.db.models import Tenant
from mockazure.identity.invitation_models import GuestPolicy
from mockazure.identity.invitations import redeem_local_invitation
from mockazure.providers.graph.body import read_json
from mockazure.security.repository import lock_security

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/guests")


@router.get("/policy")
@router.put("/policy")
async def policy(tenant_id: UUID, request: Request):
    if request.query_params:
        raise AdminError(422, "InvalidGuestPolicy", "Guest policy accepts no query options")
    body = await read_json(request) if request.method == "PUT" else None
    if body is not None and (
        set(body) != {"enabled", "allow_member_invites"}
        or any(type(v) is not bool for v in body.values())
    ):
        raise AdminError(422, "InvalidGuestPolicy", "Two Boolean policy fields are required")
    async with request.app.state.database.sessions.begin() as session:
        await lock_security(session, tenant_id)
        if not await session.get(Tenant, tenant_id):
            raise AdminError(404, "TenantNotFound", "Tenant not found")
        row = await session.get(GuestPolicy, tenant_id)
        if body is not None:
            if row is None:
                row = GuestPolicy(tenant_id=tenant_id, **body)
                session.add(row)
            else:
                row.enabled, row.allow_member_invites = (
                    body["enabled"],
                    body["allow_member_invites"],
                )
        return {
            "enabled": row.enabled if row else True,
            "allow_member_invites": row.allow_member_invites if row else True,
        }


@router.post("/{invitation_id}/redeem")
async def redeem(tenant_id: UUID, invitation_id: UUID, request: Request):
    if request.query_params or await request.body():
        raise AdminError(422, "InvalidRedemption", "Local redemption accepts no body or query")
    async with request.app.state.database.sessions.begin() as session:
        return await redeem_local_invitation(session, tenant_id, invitation_id, datetime.now(UTC))

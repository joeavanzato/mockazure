"""Authenticated local cloud fixtures; never a Microsoft endpoint."""

from uuid import UUID

from fastapi import APIRouter, Request

from mockazure.admin.mail_routes import tenant_required
from mockazure.cloud.provider_state import (
    ProviderStateInput,
    export_provider_state,
    replace_provider_state,
)

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/cloud")


@router.get("/providerState")
async def export(tenant_id: UUID, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        await tenant_required(session, tenant_id)
        return await export_provider_state(session, tenant_id)


@router.put("/providerState")
async def replace(tenant_id: UUID, value: ProviderStateInput, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        await tenant_required(session, tenant_id)
        return await replace_provider_state(session, tenant_id, value)

"""Authenticated local cloud fixtures; never a Microsoft endpoint."""

from uuid import UUID

from fastapi import APIRouter, Request

from mockazure.admin.mail_routes import tenant_required
from mockazure.cloud.provider_state import (
    ProviderStateInput,
    export_provider_state,
    replace_provider_state,
)
from mockazure.identity.keys import read_wrapping_key
from mockazure.providers.arm.resource_lifecycle import (
    ResourceStateInput,
    export_provider_resources,
    replace_provider_resources,
)
from mockazure.storage.lifecycle import (
    StorageStateInput,
    export_storage_accounts,
    replace_storage_accounts,
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


@router.get("/storageState")
async def export_storage(tenant_id: UUID, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        await tenant_required(session, tenant_id)
        return await export_storage_accounts(session, tenant_id)


@router.put("/storageState")
async def replace_storage(tenant_id: UUID, value: StorageStateInput, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        await tenant_required(session, tenant_id)
        session.info["storage_cipher"] = read_wrapping_key(request.app.state.settings.data_dir)
        return await replace_storage_accounts(session, tenant_id, value)


@router.get("/resourceState")
async def export_resources(tenant_id: UUID, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        await tenant_required(session, tenant_id)
        return await export_provider_resources(session, tenant_id)


@router.put("/resourceState")
async def replace_resources(tenant_id: UUID, value: ResourceStateInput, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        await tenant_required(session, tenant_id)
        return await replace_provider_resources(session, tenant_id, value)

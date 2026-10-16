from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import ValidationError

from mockazure.admin.errors import AdminError
from mockazure.managed_identity.schemas import (
    HostInput,
    HostUpdate,
    IdentityUpdate,
    ManagedIdentityInput,
)
from mockazure.managed_identity.service import (
    create_host,
    create_managed_identity,
    rotate_host_header,
    set_identity_enabled,
    update_host,
)
from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError

router = APIRouter(prefix="/_mock/tenants/{tenant_id}")


async def read_model(request, model):
    try:
        return model.model_validate(await read_json(request))
    except (ValidationError, GraphError):
        raise AdminError(
            422, "InvalidManagedIdentityInput", "Invalid managed identity input"
        ) from None


@router.post("/managed-identities", status_code=201)
async def create_identity(tenant_id: UUID, request: Request):
    value = await read_model(request, ManagedIdentityInput)
    async with request.app.state.database.sessions.begin() as session:
        return await create_managed_identity(session, tenant_id, value)


@router.patch("/managed-identities/{identity_id}")
async def enable_identity(tenant_id: UUID, identity_id: UUID, request: Request):
    value = await read_model(request, IdentityUpdate)
    async with request.app.state.database.sessions.begin() as session:
        return await set_identity_enabled(session, tenant_id, identity_id, value.enabled)


@router.post("/managed-identity-hosts", status_code=201)
async def create_binding(tenant_id: UUID, request: Request):
    value = await read_model(request, HostInput)
    async with request.app.state.database.sessions.begin() as session:
        return await create_host(session, tenant_id, value)


@router.patch("/managed-identity-hosts/{host_id}")
async def update_binding(tenant_id: UUID, host_id: UUID, request: Request):
    value = await read_model(request, HostUpdate)
    async with request.app.state.database.sessions.begin() as session:
        return await update_host(session, tenant_id, host_id, value)


@router.post("/managed-identity-hosts/{host_id}/rotate-header")
async def rotate(tenant_id: UUID, host_id: UUID, request: Request):
    if await request.body():
        raise AdminError(422, "InvalidManagedIdentityInput", "Header rotation takes no body")
    async with request.app.state.database.sessions.begin() as session:
        return await rotate_host_header(session, tenant_id, host_id)

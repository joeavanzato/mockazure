from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from mockazure.authorization.catalog import DIRECTORY_ROLES, GRAPH_LICENSING_ROLES
from mockazure.identity.models import (
    Application,
    DirectoryRole,
    Grant,
    ServicePrincipal,
    ServicePrincipalRole,
    User,
)
from mockazure.identity.repository import (
    IdentityError,
    IdentityRepository,
    public_application,
    public_user,
)
from mockazure.identity.types import (
    ApplicationCreate,
    CredentialCreate,
    EnabledUpdate,
    GrantCreate,
    UserCreate,
)
from mockazure.providers.graph.permissions import GRAPH_DIRECTORY_ROLES

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/identity")


@asynccontextmanager
async def transaction(request, tenant_id):
    try:
        async with request.app.state.database.sessions.begin() as session:
            repo = IdentityRepository(session)
            await repo.tenant(tenant_id)
            yield repo
    except IntegrityError as error:
        state = getattr(error.orig, "sqlstate", None)
        if state == "23505":
            raise IdentityError(
                409, "IdentityConflict", "Identity or grant already exists"
            ) from None
        if state == "23503":
            raise IdentityError(404, "IdentityNotFound", "Referenced identity not found") from None
        raise


@router.post("/users", status_code=201)
async def create_user(tenant_id: UUID, data: UserCreate, request: Request):
    async with transaction(request, tenant_id) as repo:
        return public_user(await repo.create_user(tenant_id, data))


@router.post("/applications", status_code=201)
async def create_application(tenant_id: UUID, data: ApplicationCreate, request: Request):
    async with transaction(request, tenant_id) as repo:
        return public_application(await repo.create_application(tenant_id, data))


@router.get("/{collection}")
async def list_identities(
    tenant_id: UUID,
    collection: Literal["users", "applications"],
    request: Request,
    after: UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    model = User if collection == "users" else Application
    key = model.id if model is User else model.client_id
    project = public_user if model is User else public_application
    async with transaction(request, tenant_id) as repo:
        query = select(model).where(model.tenant_id == tenant_id).order_by(key).limit(limit + 1)
        if after:
            query = query.where(key > after)
        rows = list(await repo.session.scalars(query))
        return {
            "items": [project(row) for row in rows[:limit]],
            "next_after": getattr(rows[limit - 1], key.key) if len(rows) > limit else None,
        }


@router.patch("/{collection}/{object_id}")
async def set_enabled(
    tenant_id: UUID,
    collection: Literal["users", "applications"],
    object_id: UUID,
    data: EnabledUpdate,
    request: Request,
):
    model = User if collection == "users" else Application
    async with transaction(request, tenant_id) as repo:
        row = await repo.set_enabled(tenant_id, object_id, data.enabled, model)
        return public_user(row) if model is User else public_application(row)


@router.post("/applications/{client_id}/credentials", status_code=201)
async def issue_credential(
    tenant_id: UUID, client_id: UUID, data: CredentialCreate, request: Request
):
    async with transaction(request, tenant_id) as repo:
        return await repo.add_credential(tenant_id, client_id, data)


@router.delete("/credentials/{credential_id}")
async def revoke_credential(tenant_id: UUID, credential_id: UUID, request: Request):
    async with transaction(request, tenant_id) as repo:
        await repo.revoke_credential(tenant_id, credential_id)
    return {"revoked": True}


@router.put("/grants/{grant_id}")
async def set_grant(tenant_id: UUID, grant_id: UUID, data: GrantCreate, request: Request):
    async with transaction(request, tenant_id) as repo:
        row = await repo.set_grant(tenant_id, data, grant_id)
        return {"id": row.id, "tenant_id": row.tenant_id, **data.model_dump()}


@router.delete("/grants/{grant_id}")
async def revoke_grant(tenant_id: UUID, grant_id: UUID, request: Request):
    async with transaction(request, tenant_id) as repo:
        row = await repo.owned(Grant, tenant_id, grant_id)
        await repo.session.delete(row)
    return {"revoked": True}


@router.put("/users/{user_id}/roles/{role}")
async def assign_role(tenant_id: UUID, user_id: UUID, role: str, request: Request):
    if role not in DIRECTORY_ROLES | GRAPH_DIRECTORY_ROLES | GRAPH_LICENSING_ROLES:
        raise IdentityError(422, "UnsupportedRole", "Directory role is not supported")
    async with transaction(request, tenant_id) as repo:
        await repo.owned(User, tenant_id, user_id)
        existing = await repo.session.get(DirectoryRole, (tenant_id, user_id, role))
        if existing is None:
            repo.session.add(DirectoryRole(tenant_id=tenant_id, user_id=user_id, role=role))
    return {"assigned": True}


@router.delete("/users/{user_id}/roles/{role}")
async def revoke_role(tenant_id: UUID, user_id: UUID, role: str, request: Request):
    async with transaction(request, tenant_id) as repo:
        await repo.owned(User, tenant_id, user_id)
        await repo.session.execute(
            delete(DirectoryRole).where(
                DirectoryRole.tenant_id == tenant_id,
                DirectoryRole.user_id == user_id,
                DirectoryRole.role == role,
            )
        )
    return {"revoked": True}


async def require_service_principal(repo, tenant_id, client_id):
    found = await repo.session.scalar(
        select(ServicePrincipal.id).where(
            ServicePrincipal.tenant_id == tenant_id, ServicePrincipal.client_id == client_id
        )
    )
    if found is None:
        raise IdentityError(404, "IdentityNotFound", "Service principal not found in tenant")


@router.put("/applications/{client_id}/roles/{role}")
async def assign_application_role(tenant_id: UUID, client_id: UUID, role: str, request: Request):
    if role not in DIRECTORY_ROLES | GRAPH_DIRECTORY_ROLES | GRAPH_LICENSING_ROLES:
        raise IdentityError(422, "UnsupportedRole", "Directory role is not supported")
    async with transaction(request, tenant_id) as repo:
        await require_service_principal(repo, tenant_id, client_id)
        existing = await repo.session.get(ServicePrincipalRole, (tenant_id, client_id, role))
        if existing is None:
            repo.session.add(
                ServicePrincipalRole(tenant_id=tenant_id, client_id=client_id, role=role)
            )
    return {"assigned": True}


@router.delete("/applications/{client_id}/roles/{role}")
async def revoke_application_role(tenant_id: UUID, client_id: UUID, role: str, request: Request):
    async with transaction(request, tenant_id) as repo:
        await require_service_principal(repo, tenant_id, client_id)
        await repo.session.execute(
            delete(ServicePrincipalRole).where(
                ServicePrincipalRole.tenant_id == tenant_id,
                ServicePrincipalRole.client_id == client_id,
                ServicePrincipalRole.role == role,
            )
        )
    return {"revoked": True}

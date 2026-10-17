"""Authenticated local configuration of enrollment descriptors."""

from uuid import UUID

from fastapi import APIRouter, Request, Response

from mockazure.admin.identity_routes import transaction
from mockazure.providers.graph.body import read_json
from mockazure.security.methods import enroll_method, method_user, remove_method, stored_methods

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/security")


@router.get("/users/{user_id}/authenticationMethods")
async def list_methods(tenant_id: UUID, user_id: UUID, request: Request):
    async with transaction(request, tenant_id) as repo:
        user = await method_user(repo.session, tenant_id, user_id)
        return {"items": await stored_methods(repo.session, tenant_id, user)}


@router.post("/users/{user_id}/authenticationMethods", status_code=201)
async def enroll(tenant_id: UUID, user_id: UUID, request: Request):
    body = await read_json(request)
    async with transaction(request, tenant_id) as repo:
        user = await method_user(repo.session, tenant_id, user_id)
        return await enroll_method(repo.session, tenant_id, user, body)


@router.delete("/users/{user_id}/authenticationMethods/{identifier}", status_code=204)
async def remove(tenant_id: UUID, user_id: UUID, identifier: str, request: Request):
    async with transaction(request, tenant_id) as repo:
        user = await method_user(repo.session, tenant_id, user_id)
        await remove_method(repo.session, tenant_id, user, identifier)
    return Response(status_code=204)

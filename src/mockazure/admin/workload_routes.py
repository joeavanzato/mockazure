"""Explicit local workload fixtures and authoritative access provisioning."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import Field

from mockazure.admin.errors import AdminError
from mockazure.admin.evidence_routes import tenant_session
from mockazure.providers.graph.body import read_json
from mockazure.workloads.imports import WorkloadImport, WorkloadInput
from mockazure.workloads.repository import import_workload
from mockazure.workloads.resource_access import remove_grant, set_grant

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/workloads")


@router.post("/{workload}/import")
async def import_records(tenant_id: UUID, workload: str, request: Request):
    body = await read_json(request, limit=8 * 1024 * 1024)
    try:
        batch = WorkloadImport.model_validate(body)
        async with tenant_session(request, tenant_id) as session:
            return await import_workload(session, tenant_id, workload, batch)
    except (ValueError, TypeError, OverflowError):
        raise AdminError(
            422, "InvalidWorkloadImport", "Invalid typed workload records or relationships"
        ) from None


class GrantInput(WorkloadInput):
    scope: str = Field(min_length=1, max_length=512)
    principal_kind: Literal["user", "application"]
    principal_id: UUID
    role: Literal["read", "write", "owner", "fullcontrol"] = "read"
    display_name: str | None = Field(default=None, max_length=256)


@router.post("/sharepoint/grants", status_code=201)
async def grant(tenant_id: UUID, request: Request):
    try:
        body = GrantInput.model_validate(await read_json(request, limit=16384))
    except ValueError:
        raise AdminError(422, "InvalidGrant", "Invalid local resource grant") from None
    async with tenant_session(request, tenant_id) as session:
        row = await set_grant(session, tenant_id, **body.model_dump())
        return {"id": str(row.id), **body.model_dump(mode="json")}


@router.delete("/sharepoint/grants/{grant_id}", status_code=204)
async def revoke(tenant_id: UUID, grant_id: UUID, request: Request):
    async with tenant_session(request, tenant_id) as session:
        await remove_grant(session, tenant_id, grant_id)
    return Response(status_code=204)

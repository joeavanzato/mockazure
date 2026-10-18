"""Explicit administrative retry probes; no provider caller can install faults."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text

from mockazure.admin.errors import AdminError
from mockazure.identity.models import ServicePrincipal
from mockazure.providers.graph.read_profiles import ReadResource

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/graph")


class ReadFault(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    client_id: UUID
    resource: ReadResource
    kind: Literal["next_read_429", "next_page_429"]
    retry_after: int = Field(default=1, ge=1, le=300)


@router.post("/faults", status_code=201)
async def install_fault(tenant_id: UUID, data: ReadFault, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        sp = await session.scalar(
            select(ServicePrincipal.id).where(
                ServicePrincipal.tenant_id == tenant_id,
                ServicePrincipal.client_id == data.client_id,
            )
        )
        if sp is None:
            raise AdminError(404, "IdentityNotFound", "Service principal not found in tenant")
        added = await session.scalar(
            text("""
INSERT INTO graph_read_faults(tenant_id,client_id,resource,kind,retry_after)
VALUES (:tenant,:client,:resource,:kind,:retry)
ON CONFLICT DO NOTHING RETURNING resource
"""),
            dict(
                tenant=tenant_id,
                client=data.client_id,
                resource=data.resource,
                kind=data.kind,
                retry=data.retry_after,
            ),
        )
        if added is None:
            raise AdminError(409, "FaultConflict", "A read fault is already pending for this scope")
    return {"installed": True, "resource": data.resource, "kind": data.kind}

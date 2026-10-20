"""Explicit authoritative site grants, separate from imported permission evidence."""

from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import String, cast, select

from mockazure.population.adapters.arm import json_object
from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.types import FamilyDescriptor, OperationDescriptor
from mockazure.workloads.imports import WorkloadInput
from mockazure.workloads.models import WorkloadGrant
from mockazure.workloads.resource_access import set_grant


class SiteGrant(WorkloadInput):
    id: UUID
    scope: str = Field(min_length=1, max_length=512)
    principal_kind: Literal["user", "application"]
    principal_id: UUID
    role: Literal["read", "write", "owner", "fullcontrol"] = "read"
    display_name: str | None = Field(default=None, max_length=256)


def adapters():
    def normalize(payload):
        return SiteGrant.model_validate(payload).model_dump(mode="json")

    async def write(session, ctx, pending):
        for _, payload in pending:
            value = SiteGrant.model_validate(payload)
            row = await session.scalar(
                select(WorkloadGrant).where(
                    WorkloadGrant.tenant_id == ctx.tenant_id,
                    WorkloadGrant.workload == "sharepoint",
                    WorkloadGrant.scope == value.scope,
                    WorkloadGrant.principal_kind == value.principal_kind,
                    WorkloadGrant.principal_id == value.principal_id,
                )
            )
            if row is not None:
                raise ValueError("Site grant already exists under another identity")
            row = await set_grant(session, ctx.tenant_id, **value.model_dump(exclude={"id"}))
            row.id = value.id

    g = WorkloadGrant
    payload = json_object(
        id=g.id,
        scope=g.scope,
        principal_kind=g.principal_kind,
        principal_id=g.principal_id,
        role=g.role,
        display_name=g.display_name,
    )
    return [
        PayloadAdapter(
            FamilyDescriptor(
                key="sharepoint.access",
                service="SharePoint",
                api_path="/_mock/tenants/{tenant}/workloads/sharepoint/grants",
                display_fields=["id", "scope", "principal_kind", "principal_id", "role"],
                operations=[
                    OperationDescriptor(
                        key="grant",
                        mode="fixture",
                        method="POST",
                        api_version="local-fixture-v1",
                        path_template="/_mock/tenants/{tenant}/resources/sharepoint.access/apply",
                        input_schema=SiteGrant.model_json_schema(),
                    )
                ],
                contract_refs=["src/mockazure/workloads/resource_access.py"],
                limitations=[
                    "Explicit authoritative local site access. "
                    "Permission evidence imports do not grant access."
                ],
            ),
            ReadSpec(g, g.id, payload, cast(payload, String), (g.workload == "sharepoint",)),
            normalize,
            write,
        )
    ]

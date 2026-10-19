"""Explicit local RBAC assignments for named clients; no inferred permissions."""

from uuid import UUID

from pydantic import Field
from sqlalchemy import select

from mockazure.cloud.models import ProviderRegistration
from mockazure.identity.models import ServicePrincipal
from mockazure.population.adapters.arm import json_object
from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.adapters.browsing import browser
from mockazure.population.identity_models import LabIdentity
from mockazure.population.types import FamilyDescriptor, Input, OperationDescriptor
from mockazure.providers.arm.assignments import set_assignment
from mockazure.providers.arm.contracts import builtin_roles
from mockazure.providers.arm.models import RoleAssignment
from mockazure.providers.arm.scopes import parse_scope


class Assignment(Input):
    id: UUID
    scope: str = Field(max_length=2048)
    role: str = Field(max_length=128)
    identity_ref: str = Field(min_length=1, max_length=256)


def adapters():
    roles = builtin_roles()

    def normalize(payload):
        data = Assignment.model_validate(payload)
        if data.role not in roles:
            raise ValueError("Unknown built-in ARM role")
        return {
            "id": str(data.id),
            "scope": parse_scope(data.scope).key,
            "role_id": roles[data.role]["id"],
            "identity_ref": data.identity_ref,
        }

    async def write(session, ctx, pending):
        for _, payload in pending:
            binding = await session.get(LabIdentity, (ctx.tenant_id, payload["identity_ref"]))
            if binding is None:
                raise ValueError("Named test identity not found in tenant")
            principal = await session.scalar(
                select(ServicePrincipal).where(
                    ServicePrincipal.tenant_id == ctx.tenant_id,
                    ServicePrincipal.client_id == binding.client_id,
                )
            )
            scope = parse_scope(payload["scope"])
            await set_assignment(
                session,
                ctx.tenant_id,
                scope.subscription_id,
                UUID(payload["id"]),
                scope,
                payload["role_id"],
                principal.id,
                "ServicePrincipal",
            )

    row = RoleAssignment
    identity = (
        select(LabIdentity.name)
        .where(LabIdentity.tenant_id == row.tenant_id, LabIdentity.client_id == row.client_id)
        .correlate(row)
        .scalar_subquery()
    )
    assignment = PayloadAdapter(
        FamilyDescriptor(
            key="arm.roles",
            service="Identity and permissions",
            api_path="/{scope}/providers/Microsoft.Authorization/roleAssignments/{id}",
            display_fields=["id", "scope", "role_id", "identity_ref"],
            operations=[
                OperationDescriptor(
                    key="assign",
                    mode="fixture",
                    method="PUT",
                    path_template="/_mock/tenants/{tenant}/resources/arm.roles/apply",
                    api_version="local-fixture-v1",
                    input_schema=Assignment.model_json_schema(),
                )
            ],
            contract_refs=["docs/arm-contracts.md"],
            limitations=["Explicit create-only built-in role assignment for a named local client."],
        ),
        ReadSpec(
            row,
            row.id,
            json_object(id=row.id, scope=row.scope_key, role_id=row.role_id, identity_ref=identity),
            row.created_at,
        ),
        normalize,
        write,
    )
    p = ProviderRegistration
    provider = browser(
        "arm.providers",
        "Azure management",
        "/subscriptions/{subscription}/providers/{namespace}",
        p,
        p.namespace_key,
        p.state,
        {"namespace": p.namespace_key, "registrationState": p.state},
        parent=p.subscription_id,
    )
    return [assignment, provider]

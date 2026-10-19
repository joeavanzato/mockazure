from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select

from mockazure.admin.identity_routes import transaction
from mockazure.identity.repository import IdentityError
from mockazure.identity.types import IdentityInput
from mockazure.providers.arm.assignments import assignment_view, set_assignment
from mockazure.providers.arm.contracts import builtin_roles
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import (
    inventory_view,
    lock_subscription,
    seed_group,
    seed_resources,
)
from mockazure.providers.arm.models import READER_ID, ReaderAssignment, Subscription
from mockazure.providers.arm.scopes import parse_scope

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/subscriptions")


class SubscriptionInput(IdentityInput):
    display_name: str = Field(min_length=1, max_length=200)
    state: Literal["Enabled", "Disabled"] = "Enabled"


class AssignmentInput(IdentityInput):
    client_id: UUID | None = None
    user_id: UUID | None = None

    @model_validator(mode="after")
    def one_principal(self):
        if bool(self.client_id) == bool(self.user_id):
            raise ValueError("Exactly one local application or user is required")
        return self


@router.post("", status_code=201)
async def create(tenant_id: UUID, data: SubscriptionInput, request: Request):
    async with transaction(request, tenant_id) as repo:
        row = Subscription(tenant_id=tenant_id, **data.model_dump())
        repo.session.add(row)
        await repo.session.flush()
        return {"id": row.id, "tenant_id": row.tenant_id, **data.model_dump()}


@router.put("/{subscription_id}/assignments/{assignment_id}")
async def assign(
    tenant_id: UUID,
    subscription_id: UUID,
    assignment_id: UUID,
    data: AssignmentInput,
    request: Request,
):
    async with transaction(request, tenant_id) as repo:
        subscription = await lock_subscription(repo.session, tenant_id, subscription_id)
        row = await repo.session.get(ReaderAssignment, assignment_id)
        if row and (row.tenant_id != tenant_id or row.subscription_id != subscription_id):
            raise IdentityError(404, "NotFound", "Assignment not found")
        unchanged = (
            row
            and row.role_id == READER_ID
            and row.inventory_key is None
            and (row.client_id == data.client_id and row.user_id == data.user_id)
        )
        if not unchanged:
            subscription.revision += 1
            duplicate = await repo.session.scalar(
                select(ReaderAssignment.id)
                .where(
                    ReaderAssignment.tenant_id == tenant_id,
                    ReaderAssignment.scope_key == f"/subscriptions/{subscription_id}",
                    ReaderAssignment.role_id == READER_ID,
                    ReaderAssignment.client_id == data.client_id,
                    ReaderAssignment.user_id == data.user_id,
                    ReaderAssignment.id != assignment_id,
                )
                .limit(1)
            )
            if duplicate is not None:
                raise ArmError(
                    409, "RoleAssignmentExists", "Equivalent Reader grant already exists"
                )
        if row:
            row.client_id, row.user_id = data.client_id, data.user_id
            row.group_id, row.inventory_key = None, None
            row.scope_key = f"/subscriptions/{subscription_id}"
            row.role_id = READER_ID
        else:
            repo.session.add(
                ReaderAssignment(
                    id=assignment_id,
                    tenant_id=tenant_id,
                    subscription_id=subscription_id,
                    scope_key=f"/subscriptions/{subscription_id}",
                    role_id=READER_ID,
                    **data.model_dump(),
                )
            )
        await repo.session.flush()
    return {"id": assignment_id, "role": "Reader"}


@router.delete("/{subscription_id}/assignments/{assignment_id}")
async def revoke(tenant_id: UUID, subscription_id: UUID, assignment_id: UUID, request: Request):
    async with transaction(request, tenant_id) as repo:
        subscription = await lock_subscription(repo.session, tenant_id, subscription_id)
        row = await repo.owned(ReaderAssignment, tenant_id, assignment_id)
        if row.subscription_id != subscription_id:
            raise IdentityError(404, "NotFound", "Assignment not found")
        await repo.session.delete(row)
        subscription.revision += 1
    return {"revoked": True}


class GroupInput(IdentityInput):
    location: str = Field(min_length=1, max_length=100)
    tags: dict[str, str] = Field(default_factory=dict)
    managedBy: str | None = Field(default=None, max_length=2048)


class InventoryInput(IdentityInput):
    resources: list[dict] = Field(min_length=1, max_length=1000)


class RoleInput(IdentityInput):
    principal_id: UUID
    principal_type: Literal["User", "ServicePrincipal", "Group"]
    scope: str = Field(min_length=1, max_length=2048)
    role: str = Field(min_length=1, max_length=100)

    @field_validator("role")
    @classmethod
    def known_role(cls, value):
        if value not in builtin_roles():
            raise ValueError("Unsupported built-in role")
        return value

    description: str | None = Field(default=None, max_length=1024)


@router.put("/{subscription_id}/resource-groups/{name}")
async def group_seed(
    tenant_id: UUID, subscription_id: UUID, name: str, data: GroupInput, request: Request
):
    try:
        async with transaction(request, tenant_id) as repo:
            row = await seed_group(
                repo.session, tenant_id, subscription_id, name, data.model_dump(exclude_none=True)
            )
            return inventory_view(row)
    except ValueError:
        raise ArmError(400, "InvalidRequestContent", "Invalid resource group input") from None


@router.post("/{subscription_id}/resources")
async def resource_seed(
    tenant_id: UUID, subscription_id: UUID, data: InventoryInput, request: Request
):
    try:
        async with transaction(request, tenant_id) as repo:
            rows = await seed_resources(repo.session, tenant_id, subscription_id, data.resources)
            return {"value": rows}
    except ValueError:
        raise ArmError(400, "InvalidRequestContent", "Invalid inventory input") from None


@router.put("/{subscription_id}/role-assignments/{assignment_id}")
async def role_seed(
    tenant_id: UUID, subscription_id: UUID, assignment_id: UUID, data: RoleInput, request: Request
):
    try:
        scope = parse_scope(data.scope)
        async with transaction(request, tenant_id) as repo:
            row = await set_assignment(
                repo.session,
                tenant_id,
                subscription_id,
                assignment_id,
                scope,
                builtin_roles()[data.role]["id"],
                data.principal_id,
                data.principal_type,
                data.description,
            )
            return assignment_view(row, data.principal_id, data.principal_type)
    except ValueError:
        raise ArmError(400, "InvalidScope", "Invalid role assignment scope") from None


@router.delete("/{subscription_id}/role-assignments/{assignment_id}")
async def role_revoke(
    tenant_id: UUID, subscription_id: UUID, assignment_id: UUID, request: Request
):
    async with transaction(request, tenant_id) as repo:
        subscription = await lock_subscription(repo.session, tenant_id, subscription_id)
        row = await repo.owned(ReaderAssignment, tenant_id, assignment_id)
        if row.subscription_id != subscription_id:
            raise ArmError(404, "RoleAssignmentNotFound", "Assignment was not found")
        await repo.session.delete(row)
        subscription.revision += 1
    return {"revoked": True}


class ActivityReadFault(IdentityInput):
    client_id: UUID
    kind: Literal["next_read_429", "next_page_429", "next_read_503", "next_page_503"]
    retry_after: int = Field(default=1, ge=1, le=300)


@router.post("/{subscription_id}/activity-faults", status_code=201)
async def activity_fault(
    tenant_id: UUID, subscription_id: UUID, data: ActivityReadFault, request: Request
):
    from sqlalchemy import text

    from mockazure.admin.errors import AdminError
    from mockazure.identity.models import ServicePrincipal

    async with transaction(request, tenant_id) as repo:
        await lock_subscription(repo.session, tenant_id, subscription_id)
        exists = await repo.session.scalar(
            select(ServicePrincipal.id).where(
                ServicePrincipal.tenant_id == tenant_id,
                ServicePrincipal.client_id == data.client_id,
                ServicePrincipal.deleted_at.is_(None),
            )
        )
        if exists is None:
            raise AdminError(404, "IdentityNotFound", "Local service principal not found")
        added = await repo.session.scalar(
            text("""
INSERT INTO arm_read_faults(tenant_id,subscription_id,client_id,kind,retry_after)
VALUES (:tenant,:subscription,:client,:kind,:retry)
ON CONFLICT DO NOTHING RETURNING client_id
"""),
            dict(
                tenant=tenant_id,
                subscription=subscription_id,
                client=data.client_id,
                kind=data.kind,
                retry=data.retry_after,
            ),
        )
        if added is None:
            raise AdminError(409, "FaultConflict", "An Activity fault is already pending")
    return {"installed": True, "kind": data.kind}

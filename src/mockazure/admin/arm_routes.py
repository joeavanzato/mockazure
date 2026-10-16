from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field, model_validator

from mockazure.admin.identity_routes import transaction
from mockazure.identity.repository import IdentityError
from mockazure.identity.types import IdentityInput
from mockazure.providers.arm.models import ReaderAssignment, Subscription

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
        await repo.owned(Subscription, tenant_id, subscription_id)
        row = await repo.session.get(ReaderAssignment, assignment_id)
        if row and (row.tenant_id != tenant_id or row.subscription_id != subscription_id):
            raise IdentityError(404, "NotFound", "Assignment not found")
        if row:
            row.client_id, row.user_id = data.client_id, data.user_id
        else:
            repo.session.add(
                ReaderAssignment(
                    id=assignment_id,
                    tenant_id=tenant_id,
                    subscription_id=subscription_id,
                    **data.model_dump(),
                )
            )
        await repo.session.flush()
    return {"id": assignment_id, "role": "Reader"}


@router.delete("/{subscription_id}/assignments/{assignment_id}")
async def revoke(tenant_id: UUID, subscription_id: UUID, assignment_id: UUID, request: Request):
    async with transaction(request, tenant_id) as repo:
        row = await repo.owned(ReaderAssignment, tenant_id, assignment_id)
        if row.subscription_id != subscription_id:
            raise IdentityError(404, "NotFound", "Assignment not found")
        await repo.session.delete(row)
    return {"revoked": True}

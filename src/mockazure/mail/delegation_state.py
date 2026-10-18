"""Typed tenant-local delegation export/replace/reset; never descriptive ACL imports."""

from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, model_validator
from sqlalchemy import delete, select

from mockazure.admin.errors import AdminError
from mockazure.mail.delegation import lock_delegation, resolve_trustee
from mockazure.mail.delegation_models import MailboxDelegation
from mockazure.mail.repository import resolve_mailbox
from mockazure.mail.schemas import MailInput
from mockazure.providers.graph.errors import GraphError


class DelegationInput(MailInput):
    mailbox_id: UUID
    principal_id: UUID
    permission: Literal["FullAccess", "SendAs", "SendOnBehalf"]
    deny: StrictBool = False
    inheritance: Literal["None", "All", "Children", "Descendents", "SelfAndChildren"] = "None"
    auto_mapping: StrictBool = False

    @model_validator(mode="after")
    def supported_ace(self):
        if self.permission != "FullAccess" and (
            self.deny or self.inheritance != "None" or self.auto_mapping
        ):
            raise ValueError("Only FullAccess supports deny, inheritance or auto mapping")
        return self


class DelegationState(MailInput):
    grants: list[DelegationInput] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def unique_keys(self):
        keys = [
            (v.mailbox_id, v.principal_id, v.permission, v.deny, v.inheritance) for v in self.grants
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate delegation")
        return self


async def export_delegation(session, tenant):
    await lock_delegation(session, tenant)
    rows = await session.scalars(
        select(MailboxDelegation)
        .where(MailboxDelegation.tenant_id == tenant)
        .order_by(
            MailboxDelegation.mailbox_id,
            MailboxDelegation.principal_id,
            MailboxDelegation.permission,
            MailboxDelegation.deny,
            MailboxDelegation.inheritance,
        )
    )
    return {
        "grants": [
            DelegationInput.model_validate(row, from_attributes=True).model_dump(mode="json")
            for row in rows
        ]
    }


async def replace_delegation(session, tenant, value):
    await lock_delegation(session, tenant)
    for grant in value.grants:
        try:
            await resolve_mailbox(session, tenant, str(grant.mailbox_id))
            await resolve_trustee(session, tenant, str(grant.principal_id))
        except GraphError:
            raise AdminError(
                422,
                "InvalidDelegationPrincipal",
                "Every mailbox and principal must exist in this tenant",
            ) from None
    await session.execute(delete(MailboxDelegation).where(MailboxDelegation.tenant_id == tenant))
    for grant in value.grants:
        session.add(MailboxDelegation(tenant_id=tenant, **grant.model_dump()))
    await session.flush()
    return await export_delegation(session, tenant)

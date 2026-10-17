"""Authenticated local provisioning; never contacts an external tenant."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError
from sqlalchemy import delete, select

from mockazure.admin.errors import AdminError
from mockazure.db.models import Tenant
from mockazure.identity.models import ServicePrincipal, User
from mockazure.mail.mailboxes import mailbox_view, provision_mailbox
from mockazure.mail.models import ExchangeCommandGrant, Mailbox, MailSettings
from mockazure.mail.repository import resolve_mailbox
from mockazure.mail.schemas import CommandGrantInput, MailboxInput, MailSettingsInput
from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/mail")


async def parsed(request, model):
    try:
        return model.model_validate(await read_json(request))
    except (ValidationError, GraphError, UnicodeError):
        raise AdminError(422, "InvalidMailInput", "Invalid mail management input") from None


async def tenant_required(session, tenant):
    if await session.get(Tenant, tenant) is None:
        raise AdminError(404, "TenantNotFound", "Tenant not found")


@router.put("/settings")
async def settings(tenant_id: UUID, request: Request):
    value = await parsed(request, MailSettingsInput)
    async with request.app.state.database.sessions.begin() as session:
        await tenant_required(session, tenant_id)
        row = await session.get(MailSettings, tenant_id, with_for_update=True)
        if row is None:
            row = MailSettings(tenant_id=tenant_id)
            session.add(row)
        row.exchange_enabled = value.exchange_enabled
        row.audit_tier = value.audit_tier
        return value.model_dump()


@router.put("/mailboxes/{user_id}")
async def put_mailbox(tenant_id: UUID, user_id: UUID, request: Request):
    value = await parsed(request, MailboxInput)
    async with request.app.state.database.sessions.begin() as session:
        return await provision_mailbox(session, tenant_id, user_id, value, datetime.now(UTC))


@router.get("/mailboxes/{identity}")
async def get_mailbox(tenant_id: UUID, identity: str, request: Request):
    async with request.app.state.database.sessions() as session:
        return await mailbox_view(session, await resolve_mailbox(session, tenant_id, identity))


@router.post("/grants", status_code=201)
async def create_grant(tenant_id: UUID, request: Request):
    value = await parsed(request, CommandGrantInput)
    async with request.app.state.database.sessions.begin() as session:
        await tenant_required(session, tenant_id)
        if value.user_id:
            principal = await session.scalar(
                select(User.id).where(
                    User.tenant_id == tenant_id,
                    User.id == value.user_id,
                    User.deleted_at.is_(None),
                )
            )
        else:
            principal = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant_id,
                    ServicePrincipal.client_id == value.client_id,
                    ServicePrincipal.deleted_at.is_(None),
                )
            )
        if principal is None:
            raise AdminError(404, "PrincipalNotFound", "Grant principal was not found")
        if (
            value.mailbox_user_id
            and await session.get(Mailbox, (tenant_id, value.mailbox_user_id)) is None
        ):
            raise AdminError(404, "MailboxNotFound", "Grant mailbox was not found")
        row = ExchangeCommandGrant(tenant_id=tenant_id, **value.model_dump())
        session.add(row)
        await session.flush()
        return {"id": str(row.id), **value.model_dump(mode="json")}


@router.delete("/grants/{grant_id}", status_code=204)
async def delete_grant(tenant_id: UUID, grant_id: UUID, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        await session.execute(
            delete(ExchangeCommandGrant).where(
                ExchangeCommandGrant.tenant_id == tenant_id,
                ExchangeCommandGrant.id == grant_id,
            )
        )
    return Response(status_code=204)

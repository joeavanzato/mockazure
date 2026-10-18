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


@router.post("/mailboxes/{user_id}/rules", status_code=201)
async def seed_rule(tenant_id: UUID, user_id: UUID, request: Request):
    from mockazure.directory.mutations import AdminActor
    from mockazure.mail.rules import _mutate_rule
    from mockazure.mail.schemas import RuleSeedInput

    value = await parsed(request, RuleSeedInput)
    async with request.app.state.database.sessions.begin() as session:
        mailbox = await resolve_mailbox(session, tenant_id, str(user_id))
        return await _mutate_rule(
            session,
            AdminActor(),
            mailbox,
            "create",
            None,
            value.rule,
            datetime.now(UTC),
            hidden=value.hidden,
            read_only=value.read_only,
        )


@router.post("/traces", status_code=201)
async def import_traces(tenant_id: UUID, request: Request):
    from mockazure.mail.trace import import_trace
    from mockazure.mail.trace_schemas import TraceImport

    value = await parsed(request, TraceImport)
    async with request.app.state.database.sessions.begin() as session:
        await tenant_required(session, tenant_id)
        return await import_trace(session, tenant_id, value)


@router.put("/audit-roles/{user_id}/{role}")
async def put_audit_role(tenant_id: UUID, user_id: UUID, role: str, request: Request):
    from sqlalchemy.dialects.postgresql import insert

    from mockazure.auditlog.models import AuditRole

    if role not in {"Audit Logs", "View-Only Audit Logs"}:
        raise AdminError(422, "InvalidAuditRole", "Unsupported Purview audit role")
    async with request.app.state.database.sessions.begin() as session:
        user = await session.get(User, user_id)
        if user is None or user.tenant_id != tenant_id or user.deleted_at is not None:
            raise AdminError(404, "UserNotFound", "Audit principal was not found")
        await session.execute(
            insert(AuditRole)
            .values(tenant_id=tenant_id, user_id=user_id, role=role)
            .on_conflict_do_nothing()
        )
        return {"user_id": str(user_id), "role": role}


@router.delete("/audit-roles/{user_id}/{role}", status_code=204)
async def delete_audit_role(tenant_id: UUID, user_id: UUID, role: str, request: Request):
    from mockazure.auditlog.models import AuditRole

    async with request.app.state.database.sessions.begin() as session:
        await session.execute(
            delete(AuditRole).where(
                AuditRole.tenant_id == tenant_id,
                AuditRole.user_id == user_id,
                AuditRole.role == role,
            )
        )
    return Response(status_code=204)


@router.get("/collectionState")
async def get_collection_state(tenant_id: UUID, request: Request):
    from mockazure.mail.configuration import export_collection_state

    async with request.app.state.database.sessions.begin() as session:
        return await export_collection_state(session, tenant_id)


@router.put("/collectionState")
async def put_collection_state(tenant_id: UUID, request: Request):
    from mockazure.mail.collection_schemas import ExchangeCollectionState
    from mockazure.mail.configuration import replace_collection_state

    value = await parsed(request, ExchangeCollectionState)
    async with request.app.state.database.sessions.begin() as session:
        return await replace_collection_state(session, tenant_id, value)


@router.post("/content/import")
async def import_content(tenant_id: UUID, request: Request):
    from mockazure.mail.content_models import MailContentImport
    from mockazure.mail.messages import import_mail_content

    try:
        batch = MailContentImport.model_validate(await read_json(request, limit=8 * 1024 * 1024))
    except (ValidationError, GraphError, UnicodeError):
        raise AdminError(422, "InvalidMailContent", "Invalid mailbox content import") from None
    async with request.app.state.database.sessions.begin() as session:
        return await import_mail_content(session, tenant_id, batch)


@router.get("/content/{user_id}/export")
async def export_content(tenant_id: UUID, user_id: UUID, request: Request):
    import json

    from fastapi.responses import StreamingResponse
    from starlette.background import BackgroundTask

    from mockazure.mail.messages import export_mail_content, mail_export_records, state_for

    pairs = list(request.query_params.multi_items())
    if pairs not in ([], [("format", "ndjson")]):
        raise AdminError(422, "InvalidExportFormat", "Use format=ndjson for streamed export")
    if not pairs:
        async with request.app.state.database.sessions.begin() as session:
            return await export_mail_content(session, tenant_id, user_id)
    session = request.app.state.database.sessions()
    try:
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        box = await resolve_mailbox(session, tenant_id, str(user_id))
        if await state_for(session, box) is None:
            raise AdminError(409, "StatisticsOnlyMailbox", "Mailbox has no content-backed state")

        async def body():
            try:
                async for record in mail_export_records(session, tenant_id, user_id):
                    yield (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
            finally:
                await session.close()

        return StreamingResponse(
            body(), media_type="application/x-ndjson", background=BackgroundTask(session.close)
        )
    except BaseException:
        await session.close()
        raise


@router.post("/content/{user_id}/reset")
async def reset_content(tenant_id: UUID, user_id: UUID, request: Request):
    from pydantic import BaseModel, ConfigDict, Field

    from mockazure.mail.messages import reset_mail_content

    class ResetInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        expected_revision: int = Field(ge=0, strict=True)

    value = await parsed(request, ResetInput)
    async with request.app.state.database.sessions.begin() as session:
        return await reset_mail_content(session, tenant_id, user_id, value.expected_revision)


@router.get("/delegationState")
async def get_delegation_state(tenant_id: UUID, request: Request):
    from mockazure.mail.delegation_state import export_delegation

    async with request.app.state.database.sessions.begin() as session:
        await tenant_required(session, tenant_id)
        return await export_delegation(session, tenant_id)


@router.put("/delegationState")
async def put_delegation_state(tenant_id: UUID, request: Request):
    from mockazure.mail.delegation_state import DelegationState, replace_delegation

    value = await parsed(request, DelegationState)
    async with request.app.state.database.sessions.begin() as session:
        await tenant_required(session, tenant_id)
        return await replace_delegation(session, tenant_id, value)


@router.get("/securityConfiguration")
async def get_security_configuration(tenant_id: UUID, request: Request):
    from mockazure.mail.security_configuration import export_security_configuration

    async with request.app.state.database.sessions.begin() as session:
        await tenant_required(session, tenant_id)
        return await export_security_configuration(session, tenant_id)


@router.put("/securityConfiguration")
async def put_security_configuration(tenant_id: UUID, request: Request):
    from mockazure.mail.security_configuration import replace_security_configuration
    from mockazure.mail.security_configuration_schemas import SecurityState

    value = await parsed(request, SecurityState)
    async with request.app.state.database.sessions.begin() as session:
        await tenant_required(session, tenant_id)
        return await replace_security_configuration(session, tenant_id, value)


@router.get("/roleState")
async def get_role_state(tenant_id: UUID, request: Request):
    from mockazure.mail.role_management import export_roles

    async with request.app.state.database.sessions.begin() as session:
        await tenant_required(session, tenant_id)
        return await export_roles(session, tenant_id)


@router.put("/roleState")
async def put_role_state(tenant_id: UUID, request: Request):
    from mockazure.mail.role_management import replace_roles
    from mockazure.mail.role_management_schemas import RoleState

    value = await parsed(request, RoleState)
    async with request.app.state.database.sessions.begin() as session:
        await tenant_required(session, tenant_id)
        return await replace_roles(session, tenant_id, value)

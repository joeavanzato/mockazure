"""Transactional, tenant-scoped Exchange security configuration workflows."""

from datetime import datetime
from uuid import uuid4, uuid5

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.identity.types import PrincipalContext
from mockazure.mail.authorization import require_exchange_command
from mockazure.mail.delegation import lock_delegation
from mockazure.mail.security_configuration_models import (
    ExchangeAuditSettings,
    ExchangeSecurityRecord,
)
from mockazure.mail.security_configuration_schemas import MODELS, SecurityState
from mockazure.providers.graph.errors import GraphError


async def audit_ingestion_enabled(session, tenant):
    settings = await session.get(ExchangeAuditSettings, tenant)
    return settings is None or settings.enabled


async def require_audit_ingestion(session, tenant):
    if not await audit_ingestion_enabled(session, tenant):
        raise AuthorizationDenied("Unified audit log ingestion is disabled")


async def set_ingestion(session, tenant, enabled):
    settings = await session.get(ExchangeAuditSettings, tenant)
    if settings is None:
        session.add(ExchangeAuditSettings(tenant_id=tenant, enabled=enabled))
    else:
        settings.enabled = enabled
    await session.flush()


async def rows_for(session, tenant, kind=None):
    query = select(ExchangeSecurityRecord).where(ExchangeSecurityRecord.tenant_id == tenant)
    if kind:
        query = query.where(ExchangeSecurityRecord.kind == kind)
    return list(
        await session.scalars(
            query.order_by(ExchangeSecurityRecord.kind, ExchangeSecurityRecord.id)
        )
    )


async def ensure_default(session, tenant):
    rows = await rows_for(session, tenant, "RemoteDomain")
    if not any(r.payload["DomainName"] == "*" for r in rows):
        # The catch-all remote domain is a stable configuration object, not a mailbox.
        default = MODELS["RemoteDomain"](Name="Default", DomainName="*").model_dump(mode="json")
        row = ExchangeSecurityRecord(
            tenant_id=tenant,
            id=uuid5(tenant, "exchange:remote-domain:default"),
            kind="RemoteDomain",
            name_key="default",
            payload=default,
        )
        session.add(row)
        await session.flush()


def view(row):
    return {"Identity": row.payload["Name"], "Guid": str(row.id), **row.payload}


def validate(kind, values):
    try:
        return MODELS[kind].model_validate(values).model_dump(mode="json")
    except (ValidationError, ValueError):
        raise GraphError(
            400, "InvalidParameter", "Invalid or conflicting configuration parameters"
        ) from None


async def configuration_audit(session, actor, command, identity, before, after, now):
    from mockazure.mail.facts import record_mail_fact

    await record_mail_fact(
        session, actor, command, {"configurationIdentity": identity}, before, after, now
    )


async def invoke_security_configuration(
    session: AsyncSession, actor: PrincipalContext, command: str, parameters: dict, now: datetime
) -> list[dict]:
    from mockazure.providers.exchange.commands import result_size

    await require_exchange_command(session, actor, command, tenant_wide=True)
    await lock_delegation(session, actor.tenant_id)
    verb, kind = command.split("-", 1)
    if kind == "AdminAuditLogConfig":
        before = {
            "UnifiedAuditLogIngestionEnabled": await audit_ingestion_enabled(
                session, actor.tenant_id
            )
        }
        if verb == "Set":
            after = {
                "UnifiedAuditLogIngestionEnabled": parameters.get(
                    "UnifiedAuditLogIngestionEnabled", before["UnifiedAuditLogIngestionEnabled"]
                )
            }
            # Audit eligibility is captured before this operation changes the setting.
            await configuration_audit(
                session, actor, command, "AdminAuditLogConfig", before, after, now
            )
            await set_ingestion(session, actor.tenant_id, after["UnifiedAuditLogIngestionEnabled"])
            return []
        return [before]
    await ensure_default(session, actor.tenant_id)
    rows = await rows_for(session, actor.tenant_id, kind)
    identity = parameters.get("Identity")
    targets = (
        rows
        if identity is None
        else [r for r in rows if identity.casefold() in {r.payload["Name"].casefold(), str(r.id)}]
    )
    if identity is not None and not targets:
        raise GraphError(
            404, "ManagementObjectNotFoundException", "Configuration identity was not found"
        )
    if len(targets) > 1 and identity is not None:
        raise GraphError(400, "AmbiguousIdentity", "Configuration identity is ambiguous")
    if verb == "Get":
        if kind == "OutboundConnector":
            if not parameters.get("IncludeTestModeConnectors", False):
                targets = [r for r in targets if not r.payload["TestMode"]]
            if "IsTransportRuleScoped" in parameters:
                targets = [
                    r
                    for r in targets
                    if r.payload["IsTransportRuleScoped"] == parameters["IsTransportRuleScoped"]
                ]
        limit = (
            result_size(parameters)
            if "ResultSize" in parameters
            or kind in {"RemoteDomain", "InboundConnector", "OutboundConnector"}
            else None
        )
        return [view(r) for r in targets[:limit]]
    if verb != "New" and not identity:
        raise GraphError(400, "InvalidParameter", "Identity is required")
    if verb == "New":
        body = validate(kind, {k: v for k, v in parameters.items() if k != "Force"})
        row = ExchangeSecurityRecord(
            tenant_id=actor.tenant_id,
            id=uuid4(),
            kind=kind,
            name_key=body["Name"].casefold(),
            payload=body,
        )
        before = {}
    else:
        row = targets[0]
        before = view(row)
        if (
            kind == "RemoteDomain"
            and row.payload["DomainName"] == "*"
            and (verb == "Remove" or "Name" in parameters)
        ):
            raise GraphError(
                400, "InvalidOperation", "The default remote domain cannot be removed or renamed"
            )
        if kind == "RemoteDomain":
            parameters = dict(parameters)
            if parameters.get("AllowedOOFType") == "InternalLegacy":
                if parameters.get("IsInternal") is False:
                    raise GraphError(400, "InvalidParameter", "Conflicting internal OOF settings")
                parameters["IsInternal"] = True
            elif parameters.get("IsInternal") is False:
                parameters["AllowedOOFType"] = "ExternalLegacy"
        body = (
            validate(
                kind,
                {
                    **row.payload,
                    **{k: v for k, v in parameters.items() if k not in {"Identity", "Force"}},
                },
            )
            if verb == "Set"
            else row.payload
        )
    if verb != "Remove":
        if any(
            r.id != row.id
            and (
                r.name_key == body["Name"].casefold()
                or kind == "RemoteDomain"
                and r.payload["DomainName"] == body["DomainName"]
            )
            for r in rows
        ):
            raise GraphError(
                409, "ObjectAlreadyExists", "Configuration name or remote domain already exists"
            )
        if kind == "SharingPolicy" and body["Default"]:
            for previous in rows:
                if previous.id != row.id and previous.payload["Default"]:
                    previous_before = view(previous)
                    previous.payload = {**previous.payload, "Default": False}
                    await configuration_audit(
                        session,
                        actor,
                        command,
                        previous_before["Identity"],
                        previous_before,
                        view(previous),
                        now,
                    )
        row.payload = body
        row.name_key = body["Name"].casefold()
        if verb == "New":
            session.add(row)
        after = view(row)
    else:
        after = {}
        await session.delete(row)
    await configuration_audit(
        session, actor, command, (before or after)["Identity"], before, after, now
    )
    await session.flush()
    return [after] if verb == "New" else []


async def export_security_configuration(session, tenant):
    await lock_delegation(session, tenant)
    await ensure_default(session, tenant)
    return {
        "records": [
            {"id": str(r.id), "kind": r.kind, "properties": r.payload}
            for r in await rows_for(session, tenant)
        ],
        "unified_audit_log_ingestion_enabled": await audit_ingestion_enabled(session, tenant),
    }


async def replace_security_configuration(session, tenant, state: SecurityState):
    await lock_delegation(session, tenant)
    default_id = uuid5(tenant, "exchange:remote-domain:default")
    for record in state.records:
        is_default = record.kind == "RemoteDomain" and record.properties["DomainName"] == "*"
        if (
            (record.id == default_id) != is_default
            or is_default
            and record.properties["Name"] != "Default"
            or record.kind == "RemoteDomain"
            and record.properties["Name"].casefold() == "default"
            and not is_default
        ):
            raise GraphError(400, "InvalidConfiguration", "Reserved default remote domain identity")
    await session.execute(
        delete(ExchangeSecurityRecord).where(ExchangeSecurityRecord.tenant_id == tenant)
    )
    for record in state.records:
        session.add(
            ExchangeSecurityRecord(
                tenant_id=tenant,
                id=record.id,
                kind=record.kind,
                name_key=record.properties["Name"].casefold(),
                payload=record.properties,
            )
        )
    await set_ingestion(session, tenant, state.unified_audit_log_ingestion_enabled)
    return await export_security_configuration(session, tenant)

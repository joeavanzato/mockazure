"""Indexed trace selection and documented Received/recipient continuation."""

import math
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select, text
from sqlalchemy.exc import IntegrityError

from mockazure.admin.errors import AdminError
from mockazure.authorization.roles import actor_roles, effective_roles
from mockazure.mail.authorization import require_exchange_command
from mockazure.mail.models import ExchangeCommandGrant, Mailbox, MailSettings
from mockazure.mail.trace_models import MailTrace, MailTraceDetail
from mockazure.providers.graph.errors import GraphError


def invalid(message):
    raise GraphError(400, "InvalidParameter", message)


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        invalid("An ISO8601 date with timezone is required")


def window(parameters, now):
    end = timestamp(parameters["EndDate"]) if "EndDate" in parameters else now
    start = (
        timestamp(parameters["StartDate"])
        if "StartDate" in parameters
        else end - timedelta(hours=48)
    )
    if start > end or end - start > timedelta(days=10) or start < now - timedelta(days=90):
        invalid("Trace range must be at most ten days within the last ninety days")
    if parameters.get("StartingRecipientAddress") and "EndDate" not in parameters:
        invalid("StartingRecipientAddress requires EndDate")
    return start, end


async def import_trace(session, tenant, value):
    # Serialize imports per tenant, preserving immutable collision/idempotency decisions.
    if await session.get(MailSettings, tenant, with_for_update=True) is None:
        raise AdminError(409, "MailSettingsRequired", "Configure mail before importing trace")
    inserted = 0
    for record in value.records:
        key = (tenant, record.message_trace_id, record.recipient_address)
        payload = record.model_dump(mode="json")
        existing = await session.get(MailTrace, key)
        if existing:
            if existing.payload != payload or existing.provenance != value.source:
                raise AdminError(409, "TraceConflict", "Trace recipient facts are immutable")
            continue
        if (
            record.mailbox_user_id
            and await session.get(Mailbox, (tenant, record.mailbox_user_id)) is None
        ):
            raise AdminError(404, "MailboxNotFound", "Trace mailbox scope was not found")
        columns = record.model_dump(exclude={"details"})
        for field in ("from_ip", "to_ip"):
            columns[field] = str(columns[field]) if columns[field] is not None else None
        session.add(
            MailTrace(tenant_id=tenant, provenance=value.source, payload=payload, **columns)
        )
        try:
            await session.flush()
        except IntegrityError:
            raise AdminError(
                409,
                "TraceContinuationCollision",
                "Received and recipient must uniquely identify a trace row; "
                "timestamps are not rewritten",
            ) from None
        for sequence, detail in enumerate(record.details):
            session.add(
                MailTraceDetail(
                    tenant_id=tenant,
                    message_trace_id=record.message_trace_id,
                    recipient_address=record.recipient_address,
                    sequence=sequence,
                    occurred_at=detail.date,
                    **detail.model_dump(exclude={"date"}),
                )
            )
        inserted += 1
    await session.flush()
    return {"imported": inserted, "unchanged": len(value.records) - inserted}


async def recipient_scope(session, actor, command):
    await require_exchange_command(session, actor, command)
    if actor.token_kind == "application":
        roles = await actor_roles(session, actor)
        principal = ExchangeCommandGrant.client_id == actor.client_id
    else:
        roles = await effective_roles(session, actor.tenant_id, actor.subject_id)
        principal = ExchangeCommandGrant.user_id == actor.subject_id
    if set(roles) & {"Global Administrator", "Exchange Administrator"}:
        return None
    scopes = set(
        await session.scalars(
            select(ExchangeCommandGrant.mailbox_user_id).where(
                ExchangeCommandGrant.tenant_id == actor.tenant_id,
                principal,
                ExchangeCommandGrant.commands.contains([command]),
            )
        )
    )
    return None if None in scopes else scopes


async def consume_trace_budget(session, actor, command, now):
    """Caller commits this transaction independently before the provider query."""
    await require_exchange_command(session, actor, command)
    await session.execute(
        text("""
INSERT INTO mail_trace_budgets(tenant_id,command) VALUES(:tenant,:command)
ON CONFLICT DO NOTHING
"""),
        {"tenant": actor.tenant_id, "command": command},
    )
    requests = await session.scalar(
        text("""
SELECT requests FROM mail_trace_budgets
WHERE tenant_id=:tenant AND command=:command FOR UPDATE
"""),
        {"tenant": actor.tenant_id, "command": command},
    )
    recent = [value for value in requests if value > now.timestamp() - 300]
    if len(recent) >= 100:
        return max(1, math.ceil(min(recent) + 300 - now.timestamp()))
    recent.append(now.timestamp())
    import json

    await session.execute(
        text("""
UPDATE mail_trace_budgets SET requests=CAST(:requests AS jsonb)
WHERE tenant_id=:tenant AND command=:command
"""),
        {"tenant": actor.tenant_id, "command": command, "requests": json.dumps(recent)},
    )
    return None


def trace_view(row):
    return {
        "MessageTraceId": str(row.message_trace_id),
        "Received": row.received.isoformat(),
        "SenderAddress": row.sender_address,
        "RecipientAddress": row.recipient_address,
        "Subject": row.subject,
        "Status": row.status,
        "Size": row.size,
        "MessageId": row.message_id,
        "FromIP": row.from_ip,
        "ToIP": row.to_ip,
    }


async def trace_selection(session, actor, parameters, now, command):
    scope = await recipient_scope(session, actor, command)
    start, end = window(parameters, now)
    query = select(MailTrace).where(
        MailTrace.tenant_id == actor.tenant_id,
        MailTrace.received >= start,
        MailTrace.received <= end,
    )
    if scope is not None:
        query = query.where(MailTrace.mailbox_user_id.in_(scope))
    if "MessageTraceId" in parameters:
        try:
            identifier = UUID(parameters["MessageTraceId"])
        except (ValueError, TypeError, AttributeError):
            invalid("Invalid message trace ID")
        query = query.where(MailTrace.message_trace_id == identifier)
    for key, field in (
        ("SenderAddress", MailTrace.sender_address),
        ("RecipientAddress", MailTrace.recipient_address),
        ("MessageId", MailTrace.message_id),
        ("Status", MailTrace.status),
        ("FromIP", MailTrace.from_ip),
        ("ToIP", MailTrace.to_ip),
    ):
        if key in parameters:
            values = parameters[key]
            values = [values] if isinstance(values, str) else values
            if key in {"SenderAddress", "RecipientAddress"}:
                values = [v.lower() for v in values]
            query = query.where(field.in_(values))
    return query, end


async def query_trace(session, actor, parameters: dict, now) -> list[dict]:
    query, end = await trace_selection(session, actor, parameters, now, "Get-MessageTraceV2")
    limit = parameters.get("ResultSize", 1000)
    if type(limit) is not int or not 1 <= limit <= 5000:
        invalid("ResultSize must be between one and five thousand")
    if address := parameters.get("StartingRecipientAddress"):
        query = query.where(
            or_(
                MailTrace.received < end,
                and_(MailTrace.received == end, MailTrace.recipient_address > address.lower()),
            )
        )
    if "SubjectFilterType" in parameters and "Subject" not in parameters:
        invalid("SubjectFilterType requires Subject")
    if "Subject" in parameters:
        mode = parameters.get("SubjectFilterType", "Contains")
        methods = {"Contains": "contains", "StartsWith": "startswith", "EndsWith": "endswith"}
        if mode not in methods:
            invalid("Unsupported subject filter type")
        query = query.where(
            getattr(MailTrace.subject, methods[mode])(parameters["Subject"], autoescape=True)
        )
    query = query.order_by(MailTrace.received.desc(), MailTrace.recipient_address).limit(limit)
    return [trace_view(row) for row in await session.scalars(query)]


async def query_trace_detail(session, actor, parameters: dict, now) -> list[dict]:
    if not parameters.get("MessageTraceId") or not parameters.get("RecipientAddress"):
        invalid("Trace detail requires MessageTraceId and RecipientAddress")
    query, _ = await trace_selection(session, actor, parameters, now, "Get-MessageTraceDetailV2")
    row = await session.scalar(query)
    if row is None:
        return []
    query = select(MailTraceDetail).where(
        MailTraceDetail.tenant_id == actor.tenant_id,
        MailTraceDetail.message_trace_id == row.message_trace_id,
        MailTraceDetail.recipient_address == row.recipient_address,
    )
    for key, field in (("Event", MailTraceDetail.event), ("Action", MailTraceDetail.action)):
        if key in parameters:
            query = query.where(field.in_(parameters[key]))
    return [
        {
            "MessageTraceId": str(row.message_trace_id),
            "MessageId": row.message_id,
            "RecipientAddress": row.recipient_address,
            "SenderAddress": row.sender_address,
            "Date": detail.occurred_at.isoformat(),
            "Event": detail.event,
            "Action": detail.action,
            "Detail": detail.detail,
            "Data": detail.data,
        }
        for detail in await session.scalars(query.order_by(MailTraceDetail.sequence))
    ]

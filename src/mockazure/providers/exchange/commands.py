"""Bounded Exchange command dispatch against canonical PostgreSQL state."""

from sqlalchemy import select

from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.identity.models import User
from mockazure.mail.authorization import require_exchange_command, require_mailbox_access
from mockazure.mail.mailboxes import mailbox_view, mutate_mailbox
from mockazure.mail.models import Mailbox, MailRule
from mockazure.mail.repository import resolve_mailbox
from mockazure.mail.rules import mutate_rule
from mockazure.mail.schemas import smtp_address
from mockazure.mail.sweep import mutate_sweep
from mockazure.providers.exchange.bootstrap import PARAMETERS
from mockazure.providers.exchange.serialization import (
    ACTION_NAMES,
    CONDITION_NAMES,
    RECIPIENT_FIELDS,
    exchange_rule_view,
    sweep_view,
)
from mockazure.providers.graph.errors import GraphError


def invalid(message):
    raise GraphError(400, "InvalidParameter", message)


def checked_parameters(name, values):
    if name not in PARAMETERS or not isinstance(values, dict):
        invalid("Unsupported command")
    if len(values) != len({key.lower() for key in values}):
        invalid("Duplicate parameter")
    canonical = {key.lower(): key for key in PARAMETERS[name]}
    result = {}
    for key, value in values.items():
        if key.lower() not in canonical:
            invalid("Unsupported command parameter")
        key = canonical[key.lower()]
        kind = PARAMETERS[name][key]
        if value is None:
            if key not in {"ForwardingAddress", "ForwardingSmtpAddress", "GrantSendOnBehalfTo"}:
                invalid("Null command parameter")
        elif kind in {"bool", "switch"} and type(value) is not bool:
            invalid("Boolean command parameter required")
        elif kind == "int" and (type(value) is not int or not -(2**31) <= value < 2**31):
            invalid("Integer command parameter required")
        elif kind == "string[]" and (
            not isinstance(value, (str, list))
            or isinstance(value, str)
            and (len(value) > 320 or "\0" in value)
            or isinstance(value, list)
            and any(not isinstance(v, str) or len(v) > 320 or "\0" in v for v in value)
        ):
            invalid("String collection command parameter required")
        elif kind in {"string", "datetime", "guid"} and (
            not isinstance(value, str) or len(value) > 1024 or "\0" in value
        ):
            invalid("String command parameter required")
        result[key] = [value] if kind == "string[]" and isinstance(value, str) else value
    return result


def result_size(parameters):
    value = parameters.get("ResultSize", "1000")
    if isinstance(value, str) and value.lower() == "unlimited":
        return None
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdigit()
        or not 1 <= int(value) <= 100000
    ):
        invalid("Invalid result size")
    return int(value)


async def mailbox_targets(session, actor, parameters, operation):
    if identity := parameters.get("Identity"):
        box = await resolve_mailbox(session, actor.tenant_id, identity)
        await require_mailbox_access(session, actor, box, operation)
        return [box]
    await require_exchange_command(session, actor, operation)
    query = (
        select(Mailbox)
        .join(User, (User.tenant_id == Mailbox.tenant_id) & (User.id == Mailbox.user_id))
        .where(
            Mailbox.tenant_id == actor.tenant_id,
            User.deleted_at.is_(None),
        )
        .order_by(Mailbox.user_id)
    )
    if kinds := parameters.get("RecipientTypeDetails"):
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if set(kinds) - {"UserMailbox", "SharedMailbox", "RoomMailbox", "EquipmentMailbox"}:
            invalid("Unsupported recipient type")
        query = query.where(Mailbox.mailbox_type.in_(kinds))
    # Scope before applying the caller's result limit, so invisible rows do not consume it.
    result, limit = [], result_size(parameters)
    async for box in await session.stream_scalars(query):
        try:
            await require_mailbox_access(session, actor, box, operation)
        except AuthorizationDenied:
            continue
        result.append(box)
        if limit is not None and len(result) >= limit:
            break
    return result


async def inbox_body(session, actor, parameters, *, create=False):
    result = {}
    if "Name" in parameters:
        result["displayName"] = parameters["Name"]
    if create and "Name" not in parameters:
        invalid("New-InboxRule requires Name")
    if "Priority" in parameters or create:
        priority = parameters.get("Priority", 0)
        if priority < 0:
            invalid("Priority must be nonnegative")
        result["sequence"] = priority + 1
    for target, mapping in (("actions", ACTION_NAMES), ("conditions", CONDITION_NAMES)):
        values = {}
        for graph_name, name in mapping.items():
            if name not in parameters:
                continue
            value = parameters[name]
            if name in RECIPIENT_FIELDS:
                recipients = []
                for address in [value] if isinstance(value, str) else value:
                    try:
                        normalized = smtp_address(address)
                    except ValueError:
                        normalized = (
                            await resolve_mailbox(session, actor.tenant_id, address)
                        ).primary_smtp
                    recipients.append({"emailAddress": {"address": normalized}})
                value = recipients
            values[graph_name] = value
        if values:
            result[target] = values
    return result


async def invoke_command(session, actor, name: str, parameters: dict, now) -> list[dict]:
    canonical = {key.lower(): key for key in PARAMETERS}
    if not isinstance(name, str) or name.lower() not in canonical:
        invalid("Unsupported Exchange command")
    name = canonical[name.lower()]
    parameters = checked_parameters(name, parameters)

    if name == "Get-Recipient":
        from mockazure.mail.recipients import query_recipients

        return await query_recipients(session, actor, parameters, name)
    if name in {
        "Add-MailboxPermission",
        "Remove-MailboxPermission",
        "Add-RecipientPermission",
        "Remove-RecipientPermission",
    }:
        from mockazure.mail.delegation import mutate_permission

        return await mutate_permission(session, actor, name, parameters, now)
    if name == "Search-UnifiedAuditLog":
        from mockazure.mail.unified_audit import search_unified_audit

        return await search_unified_audit(session, actor, parameters, now)
    if name in {"Get-MessageTraceV2", "Get-MessageTraceDetailV2"}:
        from mockazure.mail.trace import query_trace, query_trace_detail

        query = query_trace if name == "Get-MessageTraceV2" else query_trace_detail
        return await query(session, actor, parameters, now)
    if name in {
        "Get-OrganizationConfig",
        "Get-TransportConfig",
        "Get-AcceptedDomain",
        "Get-TransportRule",
    }:
        from mockazure.mail.configuration import read_configuration

        await require_exchange_command(session, actor, name, tenant_wide=True)
        return await read_configuration(session, actor.tenant_id, name, parameters)
    if name in {"Get-MailboxPermission", "Get-RecipientPermission"}:
        from mockazure.mail.permission_records import read_mailbox_permissions

        await require_exchange_command(session, actor, name)
        recipient = name == "Get-RecipientPermission"
        if not recipient and not parameters.get("Identity"):
            invalid("Get-MailboxPermission requires Identity")
        if "AccessRights" in parameters and parameters["AccessRights"] != ["SendAs"]:
            invalid("Only SendAs recipient access rights are supported")
        limit = result_size(parameters)
        targets = await mailbox_targets(
            session,
            actor,
            {"Identity": parameters["Identity"]}
            if parameters.get("Identity")
            else {"ResultSize": "Unlimited"},
            name,
        )
        result = []
        field = "Trustee" if recipient else "User"
        for box in targets:
            rows = await read_mailbox_permissions(session, actor, box.user_id, recipient)
            if field in parameters:
                rows = [
                    row for row in rows if row[field].casefold() == parameters[field].casefold()
                ]
            result.extend(rows)
            if limit is not None and len(result) >= limit:
                break
        return result[:limit]
    if name == "Get-Mailbox":
        return [
            await mailbox_view(session, box)
            for box in await mailbox_targets(session, actor, parameters, name)
        ]
    if name == "Set-Mailbox":
        if not parameters.get("Identity"):
            invalid("Set-Mailbox requires Identity")
        box = await resolve_mailbox(session, actor.tenant_id, parameters["Identity"])
        await mutate_mailbox(
            session, actor, box, {k: v for k, v in parameters.items() if k != "Identity"}, now
        )
        return []
    identity = parameters.get("Mailbox")
    prefix = None
    if parameters.get("Identity") and "\\" in parameters["Identity"]:
        parts = parameters["Identity"].split("\\")
        if len(parts) != 2 or not all(parts):
            invalid("Rule Identity must contain one mailbox prefix and one rule identity")
        prefix = await resolve_mailbox(session, actor.tenant_id, parts[0])
        if not identity:
            identity = str(prefix.user_id)
    if not identity and actor.token_kind == "delegated":
        identity = str(actor.subject_id)
    if not identity:
        invalid("Mailbox is required for this command")
    box = await resolve_mailbox(session, actor.tenant_id, identity)
    if prefix is not None and prefix.user_id != box.user_id:
        invalid("Rule Identity mailbox disagrees with Mailbox")
    await require_mailbox_access(session, actor, box, name)
    if not name.startswith("Get-"):
        await session.refresh(box, with_for_update=True)
    family = "sweep" if "Sweep" in name else "inbox"
    if family == "sweep" and name != "Get-SweepRule":
        return await mutate_sweep(session, actor, box, name, parameters, now)
    rows = list(
        await session.scalars(
            select(MailRule)
            .where(
                MailRule.tenant_id == actor.tenant_id,
                MailRule.user_id == box.user_id,
                MailRule.family == family,
            )
            .order_by(MailRule.sequence, MailRule.id)
            .execution_options(populate_existing=True)
        )
    )
    identifier = parameters.get("Identity")
    if identifier:
        identifier = identifier.split("\\")[-1]
        rows = [
            row
            for row in rows
            if identifier
            in (row.id, row.payload.get("Name" if family == "sweep" else "displayName"))
        ]
        if len(rows) != 1:
            raise GraphError(404, "ErrorItemNotFound", "Rule was not found uniquely")
    if name.startswith("Get-"):
        if not parameters.get("IncludeHidden", False):
            rows = [row for row in rows if not row.hidden]
        limit = result_size(parameters)
        view = sweep_view if family == "sweep" else exchange_rule_view
        return [view(box, row) for row in rows[:limit]]
    operation = {
        "New-InboxRule": "create",
        "Set-InboxRule": "update",
        "Remove-InboxRule": "delete",
        "Enable-InboxRule": "enable",
        "Disable-InboxRule": "disable",
    }[name]
    if operation != "create" and (not identifier or len(rows) != 1):
        invalid("Rule Identity is required")
    value = (
        await inbox_body(session, actor, parameters, create=operation == "create")
        if operation in {"create", "update"}
        else {}
    )
    if operation == "update":
        for group in ("actions", "conditions", "exceptions"):
            if group in value:
                value[group] = (rows[0].payload.get(group) or {}) | value[group]
    result = await mutate_rule(
        session, actor, box, operation, rows[0].id if identifier else None, value, now
    )
    if operation != "create":
        return []
    row = await session.get(MailRule, (actor.tenant_id, box.user_id, result["id"]))
    return [exchange_rule_view(box, row)]

"""Canonical mailbox recipients with explicit bounded query semantics."""

import re
from uuid import UUID

from sqlalchemy import func, literal, or_, select

from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.identity.models import User
from mockazure.mail.authorization import require_exchange_command, require_mailbox_access
from mockazure.mail.mailboxes import mailbox_view
from mockazure.mail.models import Mailbox
from mockazure.mail.repository import resolve_mailbox
from mockazure.providers.graph.errors import GraphError

RECIPIENT_FIELDS = frozenset(
    {
        "Identity",
        "Name",
        "DisplayName",
        "Alias",
        "EmailAddresses",
        "PrimarySmtpAddress",
        "UserPrincipalName",
        "ExternalDirectoryObjectId",
        "RecipientType",
        "RecipientTypeDetails",
        "ExchangeGuid",
    }
)


async def resolve_recipient(session, tenant_id: UUID, identity: str) -> dict:
    return await recipient_view(session, await resolve_mailbox(session, tenant_id, identity))


async def recipient_view(session, box):
    view = await mailbox_view(session, box)
    view["RecipientType"] = "UserMailbox"
    return {k: v for k, v in view.items() if k in RECIPIENT_FIELDS}


def filter_expression(value, *, odata=False):
    if not isinstance(value, str) or len(value) > 2048:
        raise GraphError(400, "UnsupportedFilter", "Invalid recipient filter")
    # Intentionally one predicate: unsupported Boolean/parenthesized syntax fails explicitly.
    pattern = (
        r"([A-Za-z]+)\s+" + (r"(eq|ne)" if odata else r"-(eq|ne|like)") + r"\s+'((?:[^']|'')*)'"
    )
    match = re.fullmatch(pattern, value.strip(), re.I)
    fields = {
        "displayname": User.display_name,
        "name": User.display_name,
        "alias": Mailbox.alias,
        "primarysmtpaddress": Mailbox.primary_smtp,
        "userprincipalname": User.upn,
        "recipienttypedetails": Mailbox.mailbox_type,
        "recipienttype": literal("UserMailbox"),
    }
    if not match or match[1].lower() not in fields:
        raise GraphError(
            400,
            "UnsupportedFilter",
            "Only a single eq/ne string predicate (or classic -like) is supported",
        )
    column, operator, text = (
        fields[match[1].lower()],
        match[2].lower(),
        match[3].replace("''", "'").lower(),
    )
    if operator == "like":
        if "?" in text or "[" in text or "]" in text:
            raise GraphError(400, "UnsupportedFilter", "Only * wildcards are supported")
        escaped = (
            text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("*", "%")
        )
        return func.lower(column).like(escaped, escape="\\")
    return func.lower(column) == text if operator == "eq" else func.lower(column) != text


async def query_recipients(session, actor, parameters, operation, *, after=None, odata=False):
    from mockazure.providers.exchange.commands import result_size

    await require_exchange_command(session, actor, operation)
    selectors = [
        k
        for k in (
            "Identity",
            "UserPrincipalName",
            "PrimarySmtpAddress",
            "ExternalDirectoryObjectId",
            "Anr",
        )
        if k in parameters
    ]
    if len(selectors) > 1:
        raise GraphError(
            400, "InvalidParameter", "Recipient identity selectors are mutually exclusive"
        )
    query = (
        select(Mailbox)
        .join(User, (User.tenant_id == Mailbox.tenant_id) & (User.id == Mailbox.user_id))
        .where(Mailbox.tenant_id == actor.tenant_id, User.deleted_at.is_(None))
    )
    if selectors and selectors[0] != "Anr":
        box = await resolve_mailbox(session, actor.tenant_id, parameters[selectors[0]])
        await require_mailbox_access(session, actor, box, operation)
        query = query.where(Mailbox.user_id == box.user_id)
    elif "Anr" in parameters:
        value = parameters["Anr"]
        if not value:
            raise GraphError(400, "InvalidParameter", "Anr requires text")
        query = query.where(
            or_(
                func.lower(User.display_name).contains(value.lower(), autoescape=True),
                func.lower(User.upn).contains(value.lower(), autoescape=True),
                Mailbox.alias.contains(value.lower(), autoescape=True),
            )
        )
    if "Filter" in parameters:
        query = query.where(filter_expression(parameters["Filter"], odata=odata))
    if kinds := parameters.get("RecipientTypeDetails"):
        if set(kinds) - {"UserMailbox", "SharedMailbox", "RoomMailbox", "EquipmentMailbox"}:
            raise GraphError(
                400, "UnsupportedRecipientType", "Only provisioned mailbox recipients are supported"
            )
        query = query.where(Mailbox.mailbox_type.in_(kinds))
    if kinds := parameters.get("RecipientType"):
        if set(kinds) != {"UserMailbox"}:
            raise GraphError(
                400, "UnsupportedRecipientType", "Only provisioned mailbox recipients are supported"
            )
    if after:
        query = query.where(Mailbox.user_id > after)
    sort = parameters.get("SortBy")
    if sort:
        fields = {
            "Name": User.display_name,
            "DisplayName": User.display_name,
            "Alias": Mailbox.alias,
            "PrimarySmtpAddress": Mailbox.primary_smtp,
        }
        if sort.lstrip("+-") not in fields:
            raise GraphError(400, "UnsupportedSort", "Unsupported recipient sort property")
        column = fields[sort.lstrip("+-")]
        query = query.order_by(column.desc() if sort.startswith("-") else column.asc())
    query = query.order_by(Mailbox.user_id)
    limit, rows = result_size(parameters), []
    async for box in await session.stream_scalars(query.execution_options(yield_per=256)):
        try:
            await require_mailbox_access(session, actor, box, operation)
        except AuthorizationDenied:
            continue
        rows.append(await recipient_view(session, box))
        if limit is not None and len(rows) >= limit:
            break
    return rows

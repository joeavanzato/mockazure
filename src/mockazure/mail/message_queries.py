"""Bounded Graph mail projection, SQL filtering and durable local paging."""

import base64
import copy
import hashlib
import hmac
import html
import json
import secrets
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import urlencode

from sqlalchemy import BigInteger, DateTime, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from mockazure.content.repository import object_row, stream_content
from mockazure.mail.message_models import MailContentCursor, MailMessage, MailMessageVersion
from mockazure.mail.messages import immutable_identifier
from mockazure.providers.arm.activity import instant
from mockazure.providers.graph.continuations import decode_node, encode_node
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.query.ast import Boolean, Compare
from mockazure.providers.graph.query.compiler import compile_filter
from mockazure.providers.graph.query.contracts import FieldContract, QueryContract, unsupported
from mockazure.providers.graph.query.parser import FilterParser, selection
from mockazure.providers.graph.schema_reference import schema

BASIC_HIDDEN = {
    "body",
    "bodyPreview",
    "uniqueBody",
    "attachments",
    "extensions",
    "singleValueExtendedProperties",
    "multiValueExtendedProperties",
}
MAIL_FIELDS = schema("v1.0")["types"]["message"]["properties"]
FILTER_FIELDS = {
    "receivedDateTime": FieldContract(
        "receivedDateTime", "datetime", frozenset({"eq", "ge", "gt", "le", "lt"})
    ),
    "subject": FieldContract("subject", "string", frozenset({"eq", "ne", "startswith"})),
    "isRead": FieldContract("isRead", "bool", frozenset({"eq", "ne"})),
}
CONTRACT = QueryContract(
    "v1.0", "messages", "message", FILTER_FIELDS, frozenset({"receivedDateTime", "subject"})
)


def timestamp_ticks(value):
    # PostgreSQL timestamps round at six digits. Cast only whole seconds, then
    # add the original seven-digit fraction as an integer (100ns since Unix epoch).
    whole = cast(func.regexp_replace(value, r"\.[0-9]+", ""), DateTime(timezone=True))
    fraction = func.coalesce(func.substring(value, r"\.([0-9]+)"), "")
    return cast(func.extract("epoch", whole) * 10_000_000, BigInteger) + cast(
        func.rpad(fraction, 7, "0"), BigInteger
    )


def filter_column(field, payload=None):
    value = (MailMessageVersion.payload if payload is None else payload)[field.name]
    return (
        timestamp_ticks(value.as_string())
        if field.data_type == "datetime"
        else value.as_boolean()
        if field.data_type == "bool"
        else value.as_string()
    )


def mail_filter(node, payload=None):
    if isinstance(node, Boolean):
        children = [mail_filter(child, payload) for child in node.children]
        return (and_ if node.op == "and" else or_)(*children)

    def resolver(field):
        return filter_column(field, payload)

    if isinstance(node, Compare) and node.left.name == "receivedDateTime":
        field = FILTER_FIELDS[node.left.name]
        if node.op not in field.operators:
            unsupported("Operator is not supported for this property")
        column = resolver(field)
        if node.right.value is None:
            if node.op != "eq":
                unsupported("Null only supports equality comparisons")
            return column.is_(None)
        try:
            ticks = instant(node.right.value)[1] - 621355968000000000
        except (ValueError, TypeError, OverflowError):
            unsupported("Invalid receivedDateTime literal")
        return {
            "eq": column.__eq__,
            "ge": column.__ge__,
            "gt": column.__gt__,
            "le": column.__le__,
            "lt": column.__lt__,
        }[node.op](ticks)
    return compile_filter(node, CONTRACT, resolver=resolver)


def preferences(request):
    result = {"immutable": False, "body": "html", "page_size": None}
    for preference in request.headers.get("prefer", "").split(","):
        name, _, value = preference.strip().partition("=")
        value = value.strip().strip('"')
        if name.casefold() == "idtype":
            if value != "ImmutableId":
                unsupported("Only ImmutableId preference is supported")
            result["immutable"] = True
        elif name.casefold() == "outlook.body-content-type":
            if value not in {"html", "text"}:
                unsupported("Invalid body-content-type preference")
            result["body"] = value
        elif name.casefold() == "odata.maxpagesize":
            if (
                not value.isascii()
                or not value.isdigit()
                or len(value) > 4
                or not 1 <= int(value) <= 1000
            ):
                unsupported("Page size must be1..1000")
            result["page_size"] = int(value)
    return result


def query_options(request, kind, *, item=False, delta=False):
    pairs = list(request.query_params.multi_items())
    values = dict(pairs)
    allowed = {"$select"} if item else {"$select", "$top"}
    if kind == "folders" and not item:
        allowed.add("includeHiddenFolders")
    if kind == "messages" and not item:
        allowed |= {"$filter", "$orderby", "$count"}
    if kind == "attachments" and item:
        allowed.add("$expand")
    if (
        len(values) != len(pairs)
        or values.keys() - allowed
        or sum(len(k) + len(v) for k, v in pairs) > 16384
    ):
        unsupported("Unsupported mail query option")
    fields = (
        MAIL_FIELDS
        if kind == "messages"
        else schema("v1.0")["types"]["mailFolder"]["properties"]
        if kind == "folders"
        else set().union(
            *(
                schema("v1.0")["types"][name]["properties"]
                for name in ("fileAttachment", "itemAttachment", "referenceAttachment")
            )
        )
    )
    selected = list(selection(values["$select"], fields)) if "$select" in values else None
    top = values.get("$top", "10")
    if not top.isascii() or not top.isdigit() or len(top) > 4 or not 1 <= int(top) <= 1000:
        unsupported("Mail top must be1..1000")
    if values.get("$count", "false") not in {"true", "false"} or values.get(
        "includeHiddenFolders", "false"
    ) not in {"true", "false"}:
        unsupported("Invalid boolean query option")
    filtered = FilterParser(values["$filter"]).parse() if "$filter" in values else None
    if filtered:
        mail_filter(filtered)
    ordered = []
    if "$orderby" in values:
        for expression in values["$orderby"].split(","):
            parts = expression.split()
            if (
                not 1 <= len(parts) <= 2
                or parts[0] not in CONTRACT.orderable
                or (len(parts) == 2 and parts[1] not in {"asc", "desc"})
            ):
                unsupported("Unsupported message ordering")
            if parts[0] in [x[0] for x in ordered]:
                unsupported("Duplicate order property")
            ordered.append([parts[0], parts[1] if len(parts) == 2 else "asc"])
    if ordered and filtered:

        def ordered_fields(node):
            if isinstance(node, Compare):
                return [node.left.name]
            if isinstance(node, Boolean) and node.op == "and":
                return [name for child in node.children for name in ordered_fields(child)]
            raise GraphError(
                400, "InefficientFilter", "Filter and ordering cannot be combined in this form"
            )

        sequence = list(dict.fromkeys(ordered_fields(filtered)))
        if sequence[: len(ordered)] != [name for name, _ in ordered]:
            raise GraphError(
                400, "InefficientFilter", "Order properties must lead the filter in the same order"
            )
    if "$expand" in values and values["$expand"] != "microsoft.graph.itemAttachment/item":
        unsupported("Only direct message item-attachment expansion is supported")
    if delta:
        if filtered and not (
            isinstance(filtered, Compare)
            and filtered.left.name == "receivedDateTime"
            and filtered.op in {"ge", "gt"}
        ):
            unsupported("Delta only supports receivedDateTime ge/gt")
        if ordered not in ([], [["receivedDateTime", "desc"]]) or "$count" in values:
            unsupported("Unsupported delta ordering or count")
    pref = preferences(request)
    return {
        "select": selected,
        "top": min(int(top), pref["page_size"] or 1000),
        "hidden": values.get("includeHiddenFolders") == "true",
        "filter": encode_node(filtered),
        "order": ordered,
        "count": values.get("$count") == "true",
        "expand": values.get("$expand"),
        **pref,
    }


def binding(actor, box, state, resource):
    return {
        "tenant": str(actor.tenant_id),
        "mailbox": str(box.user_id),
        "generation": str(state.generation if state else box.exchange_guid),
        "client": str(actor.client_id),
        "subject": str(actor.subject_id) if actor.subject_id else None,
        "kind": actor.token_kind,
        "permissions": hashlib.sha256("\0".join(sorted(actor.permissions)).encode()).hexdigest(),
        "resource": resource,
    }


async def issue_page(session, tenant, user, state, now, predecessor=None):
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":"))
    raw = (
        hmac.digest(predecessor.encode(), encoded.encode(), "sha256")
        if predecessor
        else secrets.token_bytes(32)
    )
    token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    digest = hashlib.sha256(token.encode()).hexdigest()
    await session.execute(
        insert(MailContentCursor)
        .values(
            token_hash=digest,
            tenant_id=tenant,
            user_id=user,
            state=state,
            expires_at=now + timedelta(hours=1),
        )
        .on_conflict_do_nothing(index_elements=["token_hash"])
    )
    return token


async def resolve_page(session, token, expected, now):
    if len(token) != 43 or not token.isascii():
        raise GraphError(400, "InvalidContinuation", "Invalid mail continuation")
    row = await session.get(MailContentCursor, hashlib.sha256(token.encode()).hexdigest())
    if row is None or row.state.get("binding") != expected:
        raise GraphError(400, "InvalidContinuation", "Mail continuation binding does not match")
    if row.expires_at <= now:
        raise GraphError(410, "SyncStateNotFound", "Mail continuation expired; restart collection")
    return copy.deepcopy(row.state)


def page_link(request, resource, token, *, delta=False):
    return (
        str(request.base_url).rstrip("/")
        + "/v1.0/"
        + resource
        + "?"
        + urlencode({"$deltatoken" if delta else "$skiptoken": token})
    )


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def body_format(body, desired):
    if body is None or body.get("contentType") == desired:
        return body
    if body.get("content") is None:
        return {**body, "contentType": desired}
    if desired == "text":
        parser = PlainText()
        parser.feed(body.get("content", ""))
        parser.close()
        return {"contentType": "text", "content": "".join(parser.parts)}
    return {
        "contentType": "html",
        "content": "<html><body>"
        + html.escape(body.get("content", "")).replace("\n", "<br>")
        + "</body></html>",
    }


def selected(value, fields):
    return {
        k: v
        for k, v in value.items()
        if k in {"id", "@odata.type"} or fields is None or k in fields
    }


def message_view(row, options, *, basic=False):
    value = copy.deepcopy(row.payload)
    revision = getattr(row, "revision", None) or row.valid_from
    value.update(
        id=immutable_identifier(row.immutable_id) if options["immutable"] else row.id,
        parentFolderId=row.folder_id,
        changeKey=str(revision),
        hasAttachments=any(not item["payload"].get("isInline", False) for item in row.attachments),
    )
    if not options["select"] or "internetMessageHeaders" not in options["select"]:
        value.pop("internetMessageHeaders", None)
    if basic:
        value = {k: v for k, v in value.items() if k not in BASIC_HIDDEN}
    value = selected(value, options["select"])
    for field in ("body", "uniqueBody"):
        if field in value:
            value[field] = body_format(value[field], options["body"])
    return value


async def folder_view(session, box, folder, metadata):
    meta = metadata.get(folder.id)
    unread = await session.scalar(
        select(func.count())
        .select_from(MailMessage)
        .where(
            MailMessage.tenant_id == box.tenant_id,
            MailMessage.user_id == box.user_id,
            MailMessage.folder_id == folder.id,
            MailMessage.payload["isRead"].as_boolean().is_(False),
        )
    )
    return {
        "id": folder.id,
        "displayName": folder.name,
        "parentFolderId": meta.parent_id if meta else None,
        "isHidden": bool(meta and meta.is_hidden),
        "childFolderCount": sum(row.parent_id == folder.id for row in metadata.values()),
        "totalItemCount": folder.item_count,
        "unreadItemCount": unread,
    }


async def attachment_view(session, tenant, value, options):
    result = {
        "@odata.type": "#microsoft.graph." + value["kind"],
        "id": value["id"],
        **copy.deepcopy(value["payload"]),
    }
    if value["kind"] == "fileAttachment" and (
        not options["select"] or "contentBytes" in options["select"]
    ):
        from uuid import UUID

        content_id = UUID(value["content_id"])
        blob = await object_row(session, tenant, content_id, finalized=True)
        if blob.size > 4 * 1024 * 1024:
            raise GraphError(
                413,
                "ErrorMessageSizeExceeded",
                "Use $select for metadata and /$value for attachments larger than4MiB",
            )
        raw = b"".join([part async for part in stream_content(session, tenant, content_id)])
        result["contentBytes"] = base64.b64encode(raw).decode()
    if value["kind"] == "itemAttachment" and options["expand"] and value.get("item"):
        result["item"] = value["item"]
    result = selected(result, options["select"])
    if options["expand"] and value.get("item"):
        result["item"] = value["item"]
    return result


async def message_page(session, box, options, upper, offset, folder_id=None):
    query = select(MailMessageVersion).where(
        MailMessageVersion.tenant_id == box.tenant_id,
        MailMessageVersion.user_id == box.user_id,
        MailMessageVersion.valid_from <= upper,
        or_(MailMessageVersion.valid_to.is_(None), MailMessageVersion.valid_to > upper),
        MailMessageVersion.deleted.is_(False),
    )
    if folder_id is not None:
        query = query.where(MailMessageVersion.folder_id == folder_id)
    filtered = decode_node(options["filter"])
    if filtered:
        query = query.where(mail_filter(filtered))
    total = (
        await session.scalar(select(func.count()).select_from(query.subquery()))
        if options["count"]
        else None
    )
    for name, direction in options["order"]:
        column = filter_column(FILTER_FIELDS[name])
        query = query.order_by(
            (column.desc() if direction == "desc" else column.asc()).nulls_last()
        )
    rows = list(
        await session.scalars(
            query.order_by(MailMessageVersion.immutable_id).offset(offset).limit(options["top"] + 1)
        )
    )
    return rows[: options["top"]], len(rows) > options["top"], total

"""Queue 2026-02-06 XML service profile."""

from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4
from xml.etree import ElementTree as ET

from sqlalchemy import select
from starlette.responses import Response

from mockazure.identity.keys import read_wrapping_key
from mockazure.providers.storage.errors import StorageError, headers
from mockazure.providers.storage.routes import bounded_body, metadata_headers, xml_response
from mockazure.storage.authorization import authorize_storage
from mockazure.storage.containers import record
from mockazure.storage.lifecycle import lock_accounts
from mockazure.storage.models import StorageAccount
from mockazure.storage.queues import (
    checked_text,
    clear_messages,
    create_queue,
    delete_message,
    enqueue_message,
    find_queue,
    invalid,
    list_queues,
    message_count,
    peek_messages,
    receive_messages,
    update_message,
)

VERSION = "2026-02-06"
SAS = {"sv", "ss", "srt", "sp", "st", "se", "sip", "spr", "sig", "si"}


def parse_body(raw):
    if b"<!" in raw or b"\x00" in raw:
        invalid("DTD and entity declarations are not supported", "InvalidXmlDocument")
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError):
        invalid("Malformed QueueMessage XML", "InvalidXmlDocument")
    if (
        root.tag != "QueueMessage"
        or root.attrib
        or len(root) != 1
        or root[0].tag != "MessageText"
        or root[0].attrib
        or len(root[0])
        or (root.text or "").strip()
        or (root[0].tail or "").strip()
    ):
        invalid("Expected QueueMessage containing MessageText", "InvalidXmlDocument")
    return checked_text(root[0].text or "")


def operation(method, path, args):
    parts = path.split("/") if path else []
    comp = args.get("comp")
    if not parts and method == "GET" and comp == "list":
        return "list", "", None, {"comp", "prefix", "marker", "maxresults", "include"}
    if len(parts) == 1:
        if comp == "metadata" and method in {"GET", "PUT"}:
            return ("read" if method == "GET" else "metadata"), parts[0], None, {"comp"}
        if comp is None and method in {"PUT", "DELETE"}:
            return ("create" if method == "PUT" else "delete"), parts[0], None, set()
    if len(parts) in {2, 3} and parts[1] == "messages" and comp is None:
        if len(parts) == 2:
            if method == "POST":
                return "enqueue", parts[0], None, {"visibilitytimeout", "messagettl"}
            if method == "DELETE":
                return "clear", parts[0], None, set()
            if method == "GET":
                if "peekonly" in args:
                    if args["peekonly"] != "true":
                        invalid("peekonly must be true")
                    return "peek", parts[0], None, {"peekonly", "numofmessages"}
                return "receive", parts[0], None, {"numofmessages", "visibilitytimeout"}
        elif method in {"PUT", "DELETE"}:
            return (
                ("update_message" if method == "PUT" else "delete_message"),
                parts[0],
                parts[2],
                {"popreceipt", "visibilitytimeout"} if method == "PUT" else {"popreceipt"},
            )
    raise StorageError(
        501, "MockAzureOperationNotImplemented", "This Queue operation is not implemented"
    )


def integer(args, name, default=None):
    value = args.get(name, default)
    if value is None:
        invalid("Missing " + name)
    if isinstance(value, str) and (not value or value.lstrip("-").isdigit() is False):
        invalid("Invalid " + name)
    return int(value)


async def dispatch_queue(request):
    settings = request.app.state.settings
    profile = settings.advertised_endpoints(request.headers.get("host", ""))
    if urlsplit(profile.queue).netloc.lower() != request.headers.get("host", "").lower():
        raise StorageError(400, "InvalidUri", "Host does not match Queue listener")
    args = request.query_params
    if len(args) != len(args.multi_items()):
        invalid("Duplicate query parameter")
    if request.headers.get("x-ms-version", args.get("sv")) != VERSION:
        invalid("Unsupported Queue version", "InvalidHeaderValue")
    if any(k.startswith("if-") for k in request.headers):
        invalid("Queue conditional headers are unsupported", "InvalidHeaderValue")
    parts = request.scope["path"].lstrip("/").split("/", 1)
    account = parts[0]
    path = parts[1] if len(parts) > 1 else ""
    action, name, message_id, allowed = operation(request.method, path, args)
    if set(args) - allowed - SAS - {"timeout"}:
        invalid("Unsupported Queue query option")
    if "timeout" in args and integer(args, "timeout") < 1:
        invalid("timeout must be positive")
    raw = await bounded_body(request, 1048576)
    text = None
    if action in {"enqueue", "update_message"} and (raw or action == "enqueue"):
        text = parse_body(raw)
    elif raw:
        invalid("This operation requires an empty body", "InvalidRequestBody")
    if action in {"update_message", "delete_message"} and not args.get("popreceipt"):
        invalid("popreceipt is required")
    async with request.app.state.database.sessions() as session:
        session.info["storage_cipher"] = read_wrapping_key(settings.data_dir)
        owner = await session.scalar(
            select(StorageAccount.tenant_id).where(StorageAccount.name == account)
        )
        if owner is None:
            raise StorageError(404, "ResourceNotFound", "Storage account was not found")
        await lock_accounts(session, owner)
        now = datetime.now(UTC)
        resource = name + (
            "/messages"
            if action in {"enqueue", "peek", "receive", "clear", "update_message", "delete_message"}
            else ""
        )
        access = await authorize_storage(session, request, "queue", action, resource, now)
        status = 204
        extra = {}
        root = None
        if action == "create":
            status = await create_queue(session, access, name, metadata_headers(request), now)
        elif action == "list":
            rows, marker = await list_queues(session, access, args, now)
            root = ET.Element("EnumerationResults", ServiceEndpoint=str(request.url).split("?")[0])
            for key, tag in (
                ("prefix", "Prefix"),
                ("marker", "Marker"),
                ("maxresults", "MaxResults"),
            ):
                if key in args:
                    ET.SubElement(root, tag).text = args[key]
            parent = ET.SubElement(root, "Queues")
            for row in rows:
                item = ET.SubElement(parent, "Queue")
                ET.SubElement(item, "Name").text = row.name
                if args.get("include") == "metadata":
                    meta = ET.SubElement(item, "Metadata")
                    for key, value in row.properties.get("metadata", {}).items():
                        ET.SubElement(meta, key).text = value
            ET.SubElement(root, "NextMarker").text = marker
            status = 200
        elif action in {"read", "metadata", "delete"}:
            row = await find_queue(session, access, name)
            if action == "read":
                extra = {"x-ms-meta-" + k: v for k, v in row.properties.get("metadata", {}).items()}
                extra["x-ms-approximate-messages-count"] = str(
                    await message_count(session, access, name, now)
                )
                status = 200
            elif action == "metadata":
                row.properties = {"metadata": metadata_headers(request)}
                row.changed_at = now
                row.etag = '"' + uuid4().hex + '"'
                await record(session, access, "queue", name, "SetQueueMetadata", now)
            else:
                await session.delete(row)
                await record(session, access, "queue", name, "DeleteQueue", now)
        elif action in {"enqueue", "peek", "receive"}:
            if action == "enqueue":
                values = [
                    await enqueue_message(
                        session,
                        access,
                        name,
                        text,
                        integer(args, "messagettl", 604800),
                        integer(args, "visibilitytimeout", 0),
                        now,
                    )
                ]
                status = 201
            elif action == "peek":
                values = await peek_messages(
                    session, access, name, integer(args, "numofmessages", 1), now
                )
                status = 200
            else:
                values = await receive_messages(
                    session,
                    access,
                    name,
                    integer(args, "numofmessages", 1),
                    integer(args, "visibilitytimeout", 30),
                    now,
                )
                status = 200
            root = ET.Element("QueueMessagesList")
            for value in values:
                item = ET.SubElement(root, "QueueMessage")
                for key, entry in value.items():
                    ET.SubElement(item, key).text = entry
        elif action == "update_message":
            extra = await update_message(
                session,
                access,
                name,
                message_id,
                args["popreceipt"],
                text,
                integer(args, "visibilitytimeout"),
                now,
            )
        elif action == "delete_message":
            await delete_message(session, access, name, message_id, args["popreceipt"], now)
        elif action == "clear":
            await clear_messages(session, access, name, now)
        await session.commit()
        return (
            xml_response(request, root, status, extra)
            if root is not None
            else Response(status_code=status, headers=headers(request) | extra)
        )

"""Graph v1.0 mailbox evidence, with ordinary authorization on every request."""

from datetime import UTC, datetime
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from mockazure.authorization.evaluator import AuthorizationDenied, authorize
from mockazure.content.repository import object_row, stream_content
from mockazure.mail.authorization import content_requirement, require_mailbox_access
from mockazure.mail.message_queries import (
    attachment_view,
    binding,
    folder_view,
    issue_page,
    message_page,
    message_view,
    page_link,
    query_options,
    resolve_page,
    selected,
)
from mockazure.mail.messages import folder_by_id, folder_rows, message_by_id, state_for
from mockazure.mail.repository import resolve_mailbox
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import context_url, principal
from mockazure.providers.graph.query.parser import selection
from mockazure.providers.graph.schema_reference import schema

router = APIRouter(prefix="/v1.0")


async def raw_content(request, *, self_path):
    # One connection owns authorization and the blob read lease until streaming ends.
    # No mailbox write lock is held while sending potentially large content.
    session = request.app.state.database.sessions()
    try:
        actor = await principal(request, session)
        if self_path and actor.token_kind != "delegated":
            raise GraphError(400, "BadRequest", "/me requires delegated authentication")
        await authorize(actor, content_requirement("raw"), session)
        identity = str(actor.subject_id) if self_path else request.path_params["user_id"]
        box = await resolve_mailbox(session, actor.tenant_id, identity)
        await require_mailbox_access(session, actor, box, "content.raw")
        message = await message_by_id(session, box, request.path_params["message_id"])
        if "folder_id" in request.path_params:
            folder = await folder_by_id(session, box, request.path_params["folder_id"])
            if message.folder_id != folder.id:
                raise GraphError(404, "ErrorItemNotFound", "Message is not in this folder")
        if request.query_params:
            raise GraphError(
                400, "Request_UnsupportedQuery", "Raw content does not accept query options"
            )
        attachment = None
        if "attachment_id" in request.path_params:
            attachment = next(
                (
                    row
                    for row in message.attachments
                    if row["id"] == request.path_params["attachment_id"]
                ),
                None,
            )
            if attachment is None:
                raise GraphError(404, "ErrorItemNotFound", "Attachment was not found")
            if attachment["kind"] == "referenceAttachment":
                raise GraphError(
                    405, "ErrorInvalidOperation", "Reference attachments have no raw content"
                )
        content_id = (
            UUID(attachment["content_id"])
            if attachment and attachment.get("content_id")
            else message.mime_content_id
            if attachment is None
            else None
        )
        if content_id is None:
            raise GraphError(404, "ErrorItemNotFound", "Raw evidence was not imported")
        blob = await object_row(session, actor.tenant_id, content_id, lock="read", finalized=True)
        content_type = (
            attachment["payload"].get("contentType") or blob.media_type
            if attachment and attachment["kind"] == "fileAttachment"
            else "message/rfc822"
        )

        async def body():
            try:
                async for data in stream_content(session, actor.tenant_id, content_id):
                    yield data
            finally:
                await session.close()

        return StreamingResponse(
            body(),
            media_type=content_type,
            headers={"Content-Length": str(blob.size)},
            background=BackgroundTask(session.close),
        )
    except AuthorizationDenied:
        await session.close()
        raise GraphError(403, "ErrorAccessDenied", "Mailbox operation is not permitted") from None
    except BaseException:
        await session.close()
        raise


def register(path, kind, *, item=False, raw=False, folder_scope=False, self_path=False):
    async def handle(request: Request):
        if raw:
            return await raw_content(request, self_path=self_path)
        try:
            async with request.app.state.database.sessions.begin() as session:
                actor = await principal(request, session)
                if self_path and actor.token_kind != "delegated":
                    raise GraphError(400, "BadRequest", "/me requires delegated authentication")
                identity = str(actor.subject_id) if self_path else request.path_params["user_id"]
                operation = (
                    "settings"
                    if kind == "settings"
                    else "raw"
                    if raw or kind == "attachments"
                    else "read"
                )
                await authorize(actor, content_requirement(operation), session)
                box = await resolve_mailbox(session, actor.tenant_id, identity)
                await require_mailbox_access(session, actor, box, "content." + operation)
                # Snapshot capture, import and cleanup all serialize on the mailbox.
                await session.refresh(box, with_for_update=True)
                state = await state_for(session, box)
                now = getattr(request.app.state, "clock", lambda: datetime.now(UTC))()
                basic = not bool(actor.permissions & {"Mail.Read", "Mail.ReadWrite"})
                folder = None
                if "folder_id" in request.path_params:
                    folder = await folder_by_id(session, box, request.path_params["folder_id"])
                message = None
                if "message_id" in request.path_params:
                    message = await message_by_id(session, box, request.path_params["message_id"])
                    if folder and message.folder_id != folder.id:
                        raise GraphError(404, "ErrorItemNotFound", "Message is not in this folder")
                if kind == "settings":
                    if not state or state.settings is None:
                        raise GraphError(
                            404, "ErrorItemNotFound", "Mailbox settings have not been imported"
                        )
                    pairs = list(request.query_params.multi_items())
                    if len(dict(pairs)) != len(pairs) or set(dict(pairs)) - {"$select"}:
                        raise GraphError(
                            400, "Request_UnsupportedQuery", "Unsupported mailbox settings query"
                        )
                    fields = (
                        selection(
                            dict(pairs)["$select"],
                            schema("v1.0")["types"]["mailboxSettings"]["properties"],
                        )
                        if pairs
                        else None
                    )
                    return selected(state.settings, fields)
                attachment = None
                if "attachment_id" in request.path_params:
                    attachment = next(
                        (
                            row
                            for row in message.attachments
                            if row["id"] == request.path_params["attachment_id"]
                        ),
                        None,
                    )
                    if attachment is None:
                        raise GraphError(404, "ErrorItemNotFound", "Attachment was not found")
                resource = "users/" + str(box.user_id)
                if folder_scope:
                    resource += "/mailFolders/" + quote(folder.id, safe="")
                if kind == "attachments":
                    resource += (
                        "/messages/"
                        + quote(request.path_params["message_id"], safe="")
                        + "/attachments"
                    )
                elif kind == "messages":
                    resource += "/messages"
                else:
                    resource += "/childFolders" if folder_scope else "/mailFolders"
                if item:
                    options = query_options(request, kind, item=True)
                    if kind == "messages":
                        return JSONResponse(
                            message_view(message, options, basic=basic),
                            headers={
                                "Preference-Applied": 'outlook.body-content-type="'
                                + options["body"]
                                + '"'
                            },
                        )
                    if kind == "attachments":
                        return await attachment_view(session, actor.tenant_id, attachment, options)
                    _, metadata = await folder_rows(session, box)
                    return selected(
                        await folder_view(session, box, folder, metadata), options["select"]
                    )
                expected = binding(actor, box, state, resource)
                token = request.query_params.get("$skiptoken")
                if token:
                    if (
                        list(request.query_params.keys()) != ["$skiptoken"]
                        or len(request.query_params.multi_items()) != 1
                    ):
                        raise GraphError(
                            400,
                            "InvalidContinuation",
                            "Use the returned mail continuation unchanged",
                        )
                    cursor = await resolve_page(session, token, expected, now)
                    if cursor.get("kind") != kind:
                        raise GraphError(400, "InvalidContinuation", "Wrong mail continuation kind")
                    options = cursor["options"]
                else:
                    options = query_options(request, kind)
                    cursor = {
                        "binding": expected,
                        "options": options,
                        "upper": state.revision if state else 0,
                        "offset": 0,
                        "kind": kind,
                    }
                total = None
                if kind == "messages":
                    rows, more, total = await message_page(
                        session,
                        box,
                        options,
                        cursor["upper"],
                        cursor["offset"],
                        folder.id if folder else None,
                    )
                    values = [message_view(row, options, basic=basic) for row in rows]
                else:
                    if "rows" not in cursor:
                        if kind == "folders":
                            folders, metadata = await folder_rows(session, box)
                            matching = [
                                row
                                for row in folders
                                if (metadata[row.id].parent_id if row.id in metadata else None)
                                == (folder.id if folder_scope else None)
                                and (
                                    options["hidden"]
                                    or not (row.id in metadata and metadata[row.id].is_hidden)
                                )
                            ]
                            cursor["rows"] = [
                                await folder_view(session, box, row, metadata) for row in matching
                            ]
                        else:
                            cursor["rows"] = message.attachments
                    start = cursor["offset"]
                    rows = cursor["rows"][start : start + options["top"]]
                    more = start + options["top"] < len(cursor["rows"])
                    values = (
                        [selected(row, options["select"]) for row in rows]
                        if kind == "folders"
                        else [
                            await attachment_view(session, actor.tenant_id, row, options)
                            for row in rows
                        ]
                    )
                result = {"@odata.context": context_url(request, resource), "value": values}
                if total is not None:
                    result["@odata.count"] = total
                if more:
                    cursor["offset"] += len(values)
                    handle = await issue_page(
                        session, actor.tenant_id, box.user_id, cursor, now, token
                    )
                    result["@odata.nextLink"] = page_link(request, resource, handle)
                return result
        except AuthorizationDenied:
            raise GraphError(
                403, "ErrorAccessDenied", "Mailbox operation is not permitted"
            ) from None

    router.add_api_route(path, handle, methods=["GET"])


for self_path in (False, True):
    prefix = "/me" if self_path else "/users/{user_id}"
    register(prefix + "/mailboxSettings", "settings", self_path=self_path)
    register(prefix + "/mailFolders", "folders", self_path=self_path)
    register(prefix + "/mailFolders/{folder_id}", "folders", item=True, self_path=self_path)
    register(
        prefix + "/mailFolders/{folder_id}/childFolders",
        "folders",
        folder_scope=True,
        self_path=self_path,
    )
    for folder_scope in (False, True):
        message_base = prefix + ("/mailFolders/{folder_id}" if folder_scope else "") + "/messages"
        register(message_base, "messages", folder_scope=folder_scope, self_path=self_path)
        register(
            message_base + "/{message_id}",
            "messages",
            item=True,
            folder_scope=folder_scope,
            self_path=self_path,
        )
        register(
            message_base + "/{message_id}/$value",
            "messages",
            raw=True,
            folder_scope=folder_scope,
            self_path=self_path,
        )
        register(
            message_base + "/{message_id}/attachments",
            "attachments",
            folder_scope=folder_scope,
            self_path=self_path,
        )
        register(
            message_base + "/{message_id}/attachments/{attachment_id}",
            "attachments",
            item=True,
            folder_scope=folder_scope,
            self_path=self_path,
        )
        register(
            message_base + "/{message_id}/attachments/{attachment_id}/$value",
            "attachments",
            raw=True,
            folder_scope=folder_scope,
            self_path=self_path,
        )

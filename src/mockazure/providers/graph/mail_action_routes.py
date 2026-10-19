"""Graph v1.0 action routes and local Outlook upload-session protocol."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.mail.attachment_uploads import create_attachment_upload, use_attachment_upload
from mockazure.mail.authorization import require_mailbox_access
from mockazure.mail.composition import attachments_from_json, changed_message
from mockazure.mail.message_queries import attachment_view, preferences
from mockazure.mail.messages import folder_by_id, message_by_id
from mockazure.mail.repository import resolve_mailbox
from mockazure.mail.send import accept_mail_action, lock_workflow, mail_fact
from mockazure.mail.workflow_contracts import validate_mail_action
from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import principal

router = APIRouter()


async def bounded_body(request, limit):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            raise GraphError(413, "Request_EntityTooLarge", "Request exceeds the local size limit")
        data.extend(chunk)
    return bytes(data)


def register(path, action, self_path):
    async def handle(request: Request):
        try:
            async with request.app.state.database.sessions.begin() as session:
                actor = await principal(request, session)
                if self_path and actor.token_kind != "delegated":
                    raise GraphError(400, "BadRequest", "/me requires delegated authentication")
                await lock_workflow(session, actor.tenant_id)
                box = await resolve_mailbox(
                    session,
                    actor.tenant_id,
                    str(actor.subject_id) if self_path else request.path_params["user_id"],
                )
                await session.refresh(box, with_for_update=True)
                if request.query_params:
                    raise GraphError(
                        400, "Request_UnsupportedQuery", "Action query options are unsupported"
                    )
                now = getattr(request.app.state, "clock", lambda: datetime.now(UTC))()
                identifier = request.path_params.get("message_id")
                source = await message_by_id(session, box, identifier) if identifier else None
                if "folder_id" in request.path_params:
                    folder = await folder_by_id(session, box, request.path_params["folder_id"])
                    if source and source.folder_id != folder.id:
                        raise GraphError(404, "ErrorItemNotFound", "Message is not in this folder")
                mime = (
                    request.headers.get("content-type", "").split(";")[0].strip().lower()
                    == "text/plain"
                )
                raw = await bounded_body(request, 8 * 1024 * 1024)
                if not raw:
                    body = {}
                elif mime:
                    body = raw
                else:
                    # Request.body caches the already bounded byte stream for the strict parser.
                    request._body = raw
                    body = await read_json(request, limit=8 * 1024 * 1024)
                if action in {"addAttachment", "deleteAttachment", "createUploadSession"}:
                    await require_mailbox_access(session, actor, box, "content.write")
                    body = validate_mail_action(action, body)
                    if action == "createUploadSession":
                        view = await create_attachment_upload(
                            session,
                            actor,
                            box,
                            source,
                            body["AttachmentItem"],
                            str(request.base_url).rstrip("/"),
                            now,
                        )
                        return JSONResponse(view, status_code=201)
                    if not source.payload.get("isDraft"):
                        raise GraphError(
                            400, "ErrorInvalidOperation", "Attachment mutations require a draft"
                        )
                    if action == "addAttachment":
                        additions = await attachments_from_json(session, actor.tenant_id, [body])
                        source.attachments = [*source.attachments, *additions]
                    else:
                        old = source.attachments
                        source.attachments = [
                            a for a in old if a["id"] != request.path_params["attachment_id"]
                        ]
                        if len(old) == len(source.attachments):
                            raise GraphError(404, "ErrorItemNotFound", "Attachment was not found")
                    await changed_message(session, box, source, now)
                    await mail_fact(session, actor, box, "Update", source, now)
                    if action == "deleteAttachment":
                        return Response(status_code=204)
                    return JSONResponse(
                        await attachment_view(
                            session,
                            actor.tenant_id,
                            additions[0],
                            {"select": None, "expand": False},
                        ),
                        status_code=201,
                    )
                result = await accept_mail_action(
                    session,
                    actor,
                    box.user_id,
                    action,
                    identifier,
                    body,
                    now,
                    folder_id=request.path_params.get("folder_id"),
                )
                if result.body is None:
                    return Response(status_code=result.status, headers=result.headers)
                value = result.body
                if preferences(request)["immutable"]:
                    from mockazure.mail.messages import immutable_identifier

                    row = await message_by_id(session, box, value["id"])
                    value["id"] = immutable_identifier(row.immutable_id)
                return JSONResponse(value, status_code=result.status, headers=result.headers)
        except AuthorizationDenied:
            raise GraphError(
                403, "ErrorAccessDenied", "Mailbox operation is not permitted"
            ) from None

    router.add_api_route(
        "/v1.0" + path, handle, methods=["DELETE" if action == "deleteAttachment" else "POST"]
    )


for self_path in (False, True):
    prefix = "/me" if self_path else "/users/{user_id}"
    register(prefix + "/sendMail", "sendMail", self_path)
    for folder in (False, True):
        collection = prefix + ("/mailFolders/{folder_id}" if folder else "") + "/messages"
        register(collection, "create", self_path)
        root = collection + "/{message_id}"
        for action in (
            "send",
            "reply",
            "replyAll",
            "forward",
            "createReply",
            "createReplyAll",
            "createForward",
        ):
            register(root + "/" + action, action, self_path)
        register(root + "/attachments", "addAttachment", self_path)
        register(root + "/attachments/{attachment_id}", "deleteAttachment", self_path)
        if not folder:
            register(root + "/attachments/createUploadSession", "createUploadSession", self_path)


@router.api_route("/_upload/mail/{token}", methods=["PUT", "GET", "DELETE"])
async def upload(request: Request, token: str):
    try:
        if request.query_params:
            raise GraphError(400, "ErrorInvalidRequest", "Do not modify the upload URL")
        if (
            request.method == "PUT"
            and request.headers.get("content-type", "").split(";")[0].strip().lower()
            != "application/octet-stream"
        ):
            raise GraphError(400, "ErrorInvalidRequest", "Upload requires application/octet-stream")
        data = await bounded_body(request, 4 * 1024 * 1024)
        async with request.app.state.database.sessions.begin() as session:
            now = getattr(request.app.state, "clock", lambda: datetime.now(UTC))()
            status, body, headers = await use_attachment_upload(
                session,
                token,
                request.method,
                data,
                request.headers.get("content-range"),
                str(request.base_url).rstrip("/"),
                now,
            )
            return (
                JSONResponse(body, status_code=status, headers=headers)
                if body is not None
                else Response(status_code=status, headers=headers)
            )
    except AuthorizationDenied:
        raise GraphError(403, "ErrorAccessDenied", "Upload authority is no longer valid") from None


@router.get(
    "/api/v2.0/Users('{user_id}')/Messages('{message_id}')/Attachments('{attachment_id}')/$value"
)
async def outlook_attachment_value(request: Request):
    # Upload completion uses the service-native Outlook Location form; raw
    # retrieval still requires the same current Graph mailbox read authority.
    from mockazure.providers.graph.mail_content_routes import raw_content

    return await raw_content(request, self_path=False)

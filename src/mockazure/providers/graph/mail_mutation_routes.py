"""Graph v1.0 mailbox mutations and folder-scoped delta."""

from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from mockazure.authorization.evaluator import AuthorizationDenied, authorize
from mockazure.mail.authorization import (
    basic_content_only,
    content_requirement,
    require_mailbox_access,
)
from mockazure.mail.message_delta import delta_page
from mockazure.mail.message_mutations import mutate_message
from mockazure.mail.message_queries import (
    binding,
    issue_page,
    message_view,
    page_link,
    preferences,
    query_options,
    resolve_page,
)
from mockazure.mail.messages import folder_by_id, immutable_identifier, message_by_id, state_for
from mockazure.mail.repository import resolve_mailbox
from mockazure.providers.graph.body import read_json, typed_body
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import context_url, principal

router = APIRouter(prefix="/v1.0")


def register(path, operation, self_path):
    async def handle(request: Request):
        try:
            async with request.app.state.database.sessions.begin() as session:
                actor = await principal(request, session)
                from mockazure.mail.send import lock_workflow

                await lock_workflow(session, actor.tenant_id)
                if self_path and actor.token_kind != "delegated":
                    raise GraphError(400, "BadRequest", "/me requires delegated authentication")
                permission = "read" if operation == "delta" else "write"
                await authorize(actor, content_requirement(permission, shared=None), session)
                identity = str(actor.subject_id) if self_path else request.path_params["user_id"]
                box = await resolve_mailbox(session, actor.tenant_id, identity)
                await require_mailbox_access(session, actor, box, "content." + permission)
                await session.refresh(box, with_for_update=True)
                now = getattr(request.app.state, "clock", lambda: datetime.now(UTC))()
                folder = (
                    await folder_by_id(session, box, request.path_params["folder_id"])
                    if "folder_id" in request.path_params
                    else None
                )
                if operation != "delta":
                    if request.query_params:
                        raise GraphError(
                            400,
                            "Request_UnsupportedQuery",
                            "Mutation query options are unsupported",
                        )
                    row = await message_by_id(session, box, request.path_params["message_id"])
                    if folder and row.folder_id != folder.id:
                        raise GraphError(404, "ErrorItemNotFound", "Message is not in this folder")
                    if operation == "delete":
                        async for part in request.stream():
                            if part:
                                raise GraphError(
                                    400, "Request_BadRequest", "Delete has no request body"
                                )
                        payload = {}
                    else:
                        payload = await read_json(request, limit=65536)
                        if operation == "update":
                            payload = typed_body(payload, "message")
                    options = {**preferences(request), "select": None}
                    result = await mutate_message(session, actor, box, row, operation, payload, now)
                    if operation == "delete":
                        return Response(status_code=204)
                    return JSONResponse(
                        message_view(result, options),
                        status_code=201 if operation in {"move", "copy"} else 200,
                    )
                state = await state_for(session, box)
                resource = (
                    f"users/{box.user_id}/mailFolders/{quote(folder.id, safe='')}/messages/delta"
                )
                expected = binding(actor, box, state, resource)
                pairs = list(request.query_params.multi_items())
                token_name = (
                    "$deltatoken" if "$deltatoken" in request.query_params else "$skiptoken"
                )
                token = request.query_params.get(token_name)
                if token is not None:
                    if len(pairs) != 1:
                        raise GraphError(
                            400, "InvalidContinuation", "Use the returned delta URL unchanged"
                        )
                    cursor = await resolve_page(session, token, expected, now)
                    if cursor.get("kind") != "delta" or cursor.get("complete") != (
                        token_name == "$deltatoken"
                    ):
                        raise GraphError(
                            400, "InvalidContinuation", "Invalid delta continuation kind"
                        )
                    if cursor["complete"]:
                        cursor.update(
                            lower=cursor["upper"],
                            upper=state.revision if state else 0,
                            offset=0,
                            complete=False,
                        )
                else:
                    cursor = {
                        "binding": expected,
                        "kind": "delta",
                        "complete": False,
                        "options": query_options(request, "messages", delta=True),
                        "lower": None,
                        "upper": state.revision if state else 0,
                        "offset": 0,
                    }
                options = cursor["options"]
                rows, more = await delta_page(
                    session,
                    box,
                    folder.id,
                    options,
                    cursor["lower"],
                    cursor["upper"],
                    cursor["offset"],
                )
                basic = basic_content_only(actor, box)
                values = [
                    {
                        "id": immutable_identifier(row.immutable_id)
                        if options["immutable"]
                        else row.id,
                        "@removed": {"reason": "deleted"},
                    }
                    if removed
                    else message_view(row, options, basic=basic)
                    for row, removed in rows
                ]
                cursor["offset"] += len(rows)
                cursor["complete"] = not more
                handle = await issue_page(session, actor.tenant_id, box.user_id, cursor, now, token)
                return {
                    "@odata.context": context_url(request, resource),
                    "value": values,
                    "@odata.nextLink" if more else "@odata.deltaLink": page_link(
                        request, resource, handle, delta=not more
                    ),
                }
        except AuthorizationDenied:
            raise GraphError(
                403, "ErrorAccessDenied", "Mailbox operation is not permitted"
            ) from None

    router.add_api_route(
        path,
        handle,
        methods=[{"update": "PATCH", "delete": "DELETE", "delta": "GET"}.get(operation, "POST")],
    )


for self_path in (False, True):
    prefix = "/me" if self_path else "/users/{user_id}"
    for function in ("delta", "delta()", "microsoft.graph.delta()"):
        register(prefix + "/mailFolders/{folder_id}/messages/" + function, "delta", self_path)
    for folder_scope in (False, True):
        root = (
            prefix + ("/mailFolders/{folder_id}" if folder_scope else "") + "/messages/{message_id}"
        )
        for operation in ("update", "delete", "move", "copy"):
            register(
                root + ("/" + operation if operation in {"move", "copy"} else ""),
                operation,
                self_path,
            )

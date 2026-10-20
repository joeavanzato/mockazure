"""Documented Graph v1.0 Teams write variants and individual membership reads."""

from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import principal, project
from mockazure.workloads.team_access import authorize_row
from mockazure.workloads.team_mutation_store import child_at
from mockazure.workloads.team_mutations import apply_team_operation, conversation_for

router = APIRouter(prefix="/v1.0")
BASES = {
    "team": "/teams/{team}",
    "channel": "/teams/{team}/channels/{channel}",
    "chat": "/chats/{chat}",
}
MESSAGE_BASES = {
    "channel": BASES["channel"] + "/messages",
    "reply": BASES["channel"] + "/messages/{message}/replies",
    "chat": BASES["chat"] + "/messages",
}


def endpoint(operation, status):
    async def handle(request: Request):
        ids = dict(request.path_params)
        if any(
            not value
            or len(value.encode("utf-8")) > 512
            or value in {".", ".."}
            or any(c in value for c in "/\\\0")
            for value in ids.values()
        ):
            raise GraphError(400, "BadRequest", "Invalid Teams path identity")
        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            if operation.endswith(".member.get"):
                category = operation.split(".")[1]
                _, conversation = await conversation_for(session, actor, category, ids)
                await authorize_row(session, actor, conversation, category + "Members")
                row = await child_at(
                    session, actor.tenant_id, conversation, "members", ids["member"]
                )
                return project(request, row.payload)
            if request.query_params:
                raise GraphError(
                    400, "Request_UnsupportedQuery", "Write query options are unsupported"
                )
            body = await read_json(request, limit=65536, allow_empty=True)
            value = await apply_team_operation(
                session,
                actor,
                operation,
                ids.get("chat", ids.get("team", "")),
                ids,
                body,
                datetime.now(UTC),
            )
            if status == 204:
                return Response(status_code=204)
            headers = {}
            if operation.endswith(".member.add"):
                headers["Location"] = (
                    str(request.url).rstrip("/") + "/" + quote(value["id"], safe="")
                )
            return JSONResponse(value, status_code=status, headers=headers)

    return handle


router.add_api_route("/chats", endpoint("teams.chat.create", 201), methods=["POST"])
for category, base in BASES.items():
    for action, method, suffix, status in (
        ("add", "POST", "/members", 201),
        ("get", "GET", "/members/{member}", 200),
        ("update", "PATCH", "/members/{member}", 200),
        ("delete", "DELETE", "/members/{member}", 204),
    ):
        if category == "chat" and action == "update":
            continue  # No documented Graph v1.0 operation.
        operation = f"teams.{category}.member.{action}"
        router.add_api_route(
            base + suffix, endpoint(operation, status), methods=[method], name=operation
        )
for category, base in MESSAGE_BASES.items():
    operation = f"teams.{category}.message.send"
    router.add_api_route(base, endpoint(operation, 201), methods=["POST"], name=operation)
    operation = f"teams.{category}.message.update"
    router.add_api_route(
        base + "/{item}", endpoint(operation, 204), methods=["PATCH"], name=operation
    )
    for action, segment in (("softdelete", "softDelete"), ("undosoftdelete", "undoSoftDelete")):
        operation = f"teams.{category}.message.{action}"
        path = ("/users/{user}" + base if category == "chat" else base) + "/{item}/" + segment
        router.add_api_route(path, endpoint(operation, 204), methods=["POST"], name=operation)

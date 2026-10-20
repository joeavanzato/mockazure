"""Native Graph file operations and persisted opaque copy-monitor URLs."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from mockazure.providers.graph.body import read_json, typed_body
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import principal
from mockazure.workloads import drive_mutations
from mockazure.workloads.models import WorkloadCursor

router = APIRouter()


def origin(request):
    return request.app.state.settings.advertised_endpoints(request.headers.get("host", "")).graph


def identities(*values):
    if any(
        not value
        or len(value.encode("utf-8")) > 512
        or value in {".", ".."}
        or any(ord(c) < 32 or c in "/\\" for c in value)
        for value in values
    ):
        raise GraphError(400, "invalidRequest", "Invalid drive identity")


def endpoint(operation):
    async def handle(request: Request):
        values = request.path_params
        drive, item = values["drive"], values.get("item", values.get("parent"))
        identities(drive, item)
        if request.query_params:
            raise GraphError(400, "invalidRequest", "Write query options are unsupported")
        now = datetime.now(UTC)
        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            ctx = await drive_mutations.context(session, actor, drive, item)
            if operation == "upload":
                data = bytearray()
                async for chunk in request.stream():
                    if len(data) + len(chunk) > 8 * 1024 * 1024:
                        raise GraphError(413, "maxFileSizeExceeded", "Local upload limit is 8 MiB")
                    data.extend(chunk)
                media = request.headers.get("content-type", "application/octet-stream").split(";")[
                    0
                ]
                value, status = await drive_mutations.upload(
                    session,
                    actor,
                    ctx,
                    bytes(data),
                    media,
                    now,
                    name=values.get("name"),
                    if_match=request.headers.get("if-match"),
                )
                return JSONResponse(value, status_code=status, headers={"ETag": value["eTag"]})
            body = await read_json(request, limit=65536, allow_empty=operation == "delete")
            if operation == "update":
                body = typed_body(body, "driveItem")
            result = await drive_mutations.change(
                session,
                actor,
                ctx,
                operation,
                body,
                now,
                if_match=request.headers.get("if-match"),
            )
            if operation == "delete":
                return Response(status_code=204)
            if operation == "copy":
                token = secrets.token_hex(32)
                session.add(
                    WorkloadCursor(
                        token_hash=hashlib.sha256(token.encode()).hexdigest(),
                        tenant_id=actor.tenant_id,
                        workload="sharepoint",
                        expires_at=now + timedelta(hours=1),
                        state={
                            "purpose": "drive-copy",
                            "client": str(actor.client_id),
                            "user": str(actor.subject_id),
                            "kind": actor.token_kind,
                            "drive": drive,
                            "item": result["id"],
                            "generation": str(ctx["state"].generation),
                        },
                    )
                )
                return Response(
                    status_code=202, headers={"Location": origin(request) + "/drive-copy/" + token}
                )
            return JSONResponse(result, headers={"ETag": result["eTag"]})

    return handle


router.add_api_route(
    "/v1.0/drives/{drive}/items/{parent}:/{name}:/content", endpoint("upload"), methods=["PUT"]
)
router.add_api_route(
    "/v1.0/drives/{drive}/items/{item}/content", endpoint("upload"), methods=["PUT"]
)
router.add_api_route("/v1.0/drives/{drive}/items/{item}", endpoint("update"), methods=["PATCH"])
router.add_api_route("/v1.0/drives/{drive}/items/{item}", endpoint("delete"), methods=["DELETE"])
router.add_api_route("/v1.0/drives/{drive}/items/{item}/copy", endpoint("copy"), methods=["POST"])


@router.get("/drive-copy/{token}")
async def monitor(token: str, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        row = await session.get(WorkloadCursor, hashlib.sha256(token.encode()).hexdigest())
        if (
            row is None
            or row.tenant_id != actor.tenant_id
            or row.state.get("purpose") != "drive-copy"
            or row.expires_at <= datetime.now(UTC)
        ):
            raise GraphError(404, "itemNotFound", "Copy operation was not found")
        data = row.state
        if (data["client"], data["user"], data["kind"]) != (
            str(actor.client_id),
            str(actor.subject_id),
            actor.token_kind,
        ):
            raise GraphError(403, "accessDenied", "Copy monitor belongs to another identity")
        ctx = await drive_mutations.context(session, actor, data["drive"], data["item"])
        if str(ctx["state"].generation) != data["generation"]:
            raise GraphError(410, "resyncRequired", "Copy operation belongs to a reset scope")
        location = origin(request) + "/v1.0/drives/" + quote(data["drive"], safe="")
        location += "/items/" + quote(data["item"], safe="")
        return RedirectResponse(location, status_code=303)

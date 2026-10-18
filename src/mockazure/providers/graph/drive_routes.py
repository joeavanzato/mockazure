"""Graph v1.0 drive evidence and local capability byte streaming."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import and_, select
from starlette.background import BackgroundTask

from mockazure.admin.errors import AdminError
from mockazure.content.ranges import content_range
from mockazure.content.repository import object_row, stream_content
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import principal
from mockazure.providers.graph.site_routes import missing
from mockazure.workloads.drives import issue_download, resolve_drive_item, verify_download
from mockazure.workloads.models import WorkloadRelation as E
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.queries import collection_response, invalid, options, page_state, project
from mockazure.workloads.repository import current_record, visible
from mockazure.workloads.resource_access import authorize_operation, authorize_site

router = APIRouter()


async def item_projection(request, session, actor, row, opts):
    value = project(row, opts["select"])
    # Import remains evidence: do not fetch webUrl or remote-source annotations.
    return value


@router.get("/content/{token}")
async def download(token: str, request: Request):
    if request.query_params:
        invalid()
    session = request.app.state.database.sessions()
    try:
        receipt = await verify_download(session, token, datetime.now(UTC))
        if receipt is None:
            raise GraphError(403, "accessDenied", "Download capability is invalid or expired")
        blob = await object_row(
            session, receipt.tenant_id, receipt.content_id, lock="read", finalized=True
        )
        start, end = content_range(request.headers.get("range"), blob.size)
        headers = {
            "Content-Length": str(end - start),
            "Accept-Ranges": "bytes",
            "ETag": '"' + blob.sha256 + '"',
        }
        status = 206 if request.headers.get("range") else 200
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end - 1}/{blob.size}"

        async def body():
            try:
                async for chunk in stream_content(
                    session, receipt.tenant_id, receipt.content_id, start, end
                ):
                    yield chunk
            finally:
                await session.close()

        return StreamingResponse(
            body(),
            status_code=status,
            headers=headers,
            media_type=blob.media_type,
            background=BackgroundTask(session.close),
        )
    except BaseException as error:
        await session.close()
        if isinstance(error, AdminError):
            raise GraphError(
                error.status,
                "itemNotFound" if error.status == 404 else "invalidRange",
                error.message,
            ) from None
        raise


@router.get("/v1.0/drives/{drive_path:path}")
async def drive_endpoint(drive_path: str, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        await authorize_operation(session, actor, "drive")
        drive_id, _, tail = drive_path.partition("/")
        drive = await current_record(session, actor.tenant_id, "sharepoint", "drive", drive_id)
        if drive is None:
            missing()
        await authorize_site(session, actor, drive.scope, "drive")
        if not tail:
            return project(drive, options(request, "drive", item=True)["select"])
        if tail in {"root/delta", "root/delta()", "root/microsoft.graph.delta()"}:
            from mockazure.workloads.drive_delta import delta_response

            return await delta_response(request, session, actor, drive)
        by_path = False
        if tail.startswith("root:"):
            raw = tail[5:]
            if not raw.startswith("/"):
                invalid("Invalid drive path")
            path, separator, operation = raw[1:].partition(":")
            if not separator:
                path, operation = raw[1:], ""
            identifier = path
            by_path = True
            operation = operation.lstrip("/")
        elif tail == "root" or tail.startswith("root/"):
            identifier, _, operation = tail.partition("/")
        elif tail.startswith("items/"):
            identifier, _, operation = tail[6:].partition("/")
        else:
            raise GraphError(
                501, "MockAzureOperationNotImplemented", "Drive operation is not implemented"
            )
        resolved = await resolve_drive_item(
            session, actor.tenant_id, drive_id, identifier, by_path=by_path
        )
        item = resolved["row"]
        if not operation:
            return await item_projection(
                request, session, actor, item, options(request, "driveItem", item=True)
            )
        parts = operation.split("/")
        if parts[0] == "content":
            if len(parts) != 1 or request.query_params:
                invalid()
            if "file" not in item.payload or item.content_id is None:
                raise GraphError(404, "itemNotFound", "File content was not imported")
            token = await issue_download(session, actor, item.content_id, datetime.now(UTC))
            origin = request.app.state.settings.advertised_endpoints(
                request.headers.get("host", "")
            ).graph
            return RedirectResponse(origin + "/content/" + token, status_code=302)
        kind = {
            "children": "driveItem",
            "permissions": "permission",
            "versions": "driveItemVersion",
        }.get(parts[0])
        if kind is None or (parts[0] == "children" and len(parts) > 1) or len(parts) > 3:
            raise GraphError(
                501, "MockAzureOperationNotImplemented", "Drive relationship is not implemented"
            )
        if parts[0] == "children" and "folder" not in item.payload:
            invalid("Only folders have children")
        if len(parts) > 1:
            row = await session.scalar(
                select(V)
                .join(
                    E,
                    and_(
                        E.tenant_id == V.tenant_id,
                        E.workload == V.workload,
                        E.scope == V.scope,
                        E.target_id == V.id,
                        E.target_kind == V.kind,
                    ),
                )
                .where(
                    V.tenant_id == actor.tenant_id,
                    V.workload == "sharepoint",
                    V.scope == drive.scope,
                    V.kind == kind,
                    V.id == parts[1],
                    V.valid_to.is_(None),
                    V.deleted.is_(False),
                    E.source_kind == "driveItem",
                    E.source_id == item.id,
                    E.relation == parts[0],
                    E.valid_to.is_(None),
                )
            )
            if row is None:
                missing()
            if len(parts) == 3:
                if parts[0] != "versions" or parts[2] != "content" or request.query_params:
                    invalid()
                if row.content_id is None:
                    missing()
                token = await issue_download(session, actor, row.content_id, datetime.now(UTC))
                origin = request.app.state.settings.advertised_endpoints(
                    request.headers.get("host", "")
                ).graph
                return RedirectResponse(origin + "/content/" + token, status_code=302)
            return project(row, options(request, kind, item=True)["select"])
        page, token = await page_state(request, session, actor, kind, "drive", drive.scope)
        revision = page["scopes"][drive.scope]["upper"]
        query = (
            select(V)
            .join(
                E,
                and_(
                    E.tenant_id == V.tenant_id,
                    E.workload == V.workload,
                    E.scope == V.scope,
                    E.target_kind == V.kind,
                    E.target_id == V.id,
                ),
            )
            .where(
                V.tenant_id == actor.tenant_id,
                V.workload == "sharepoint",
                V.scope == drive.scope,
                V.kind == kind,
                V.deleted.is_(False),
                visible(V, revision),
                E.source_kind == "driveItem",
                E.source_id == item.id,
                E.relation == parts[0],
                visible(E, revision),
            )
        )
        return await collection_response(request, session, actor, query, kind, page, token)

"""Explicit local workload fixtures and authoritative access provisioning."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import Field

from mockazure.admin.errors import AdminError
from mockazure.admin.evidence_routes import tenant_session
from mockazure.providers.graph.body import read_json
from mockazure.workloads.imports import WorkloadImport, WorkloadInput
from mockazure.workloads.repository import import_workload
from mockazure.workloads.resource_access import remove_grant, set_grant

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/workloads")


@router.post("/{workload}/import")
async def import_records(tenant_id: UUID, workload: str, request: Request):
    body = await read_json(request, limit=8 * 1024 * 1024)
    try:
        batch = WorkloadImport.model_validate(body)
        async with tenant_session(request, tenant_id) as session:
            return await import_workload(session, tenant_id, workload, batch)
    except (ValueError, TypeError, OverflowError):
        raise AdminError(
            422, "InvalidWorkloadImport", "Invalid typed workload records or relationships"
        ) from None


class GrantInput(WorkloadInput):
    scope: str = Field(min_length=1, max_length=512)
    principal_kind: Literal["user", "application"]
    principal_id: UUID
    role: Literal["read", "write", "owner", "fullcontrol"] = "read"
    display_name: str | None = Field(default=None, max_length=256)


@router.post("/sharepoint/grants", status_code=201)
async def grant(tenant_id: UUID, request: Request):
    try:
        body = GrantInput.model_validate(await read_json(request, limit=16384))
    except ValueError:
        raise AdminError(422, "InvalidGrant", "Invalid local resource grant") from None
    async with tenant_session(request, tenant_id) as session:
        row = await set_grant(session, tenant_id, **body.model_dump())
        return {"id": str(row.id), **body.model_dump(mode="json")}


@router.delete("/sharepoint/grants/{grant_id}", status_code=204)
async def revoke(tenant_id: UUID, grant_id: UUID, request: Request):
    async with tenant_session(request, tenant_id) as session:
        await remove_grant(session, tenant_id, grant_id)
    return Response(status_code=204)


@router.get("/{workload}/export")
async def export(tenant_id: UUID, workload: str, request: Request):
    import json

    from fastapi.responses import StreamingResponse

    from mockazure.workloads.lifecycle import (
        check_archive_size,
        export_archive,
        export_records,
        export_workload,
    )

    values = dict(request.query_params)
    if (
        len(values) != len(request.query_params.multi_items())
        or set(values) - {"scope", "format"}
        or not values.get("scope")
        or values.get("format") not in {None, "ndjson", "zip"}
    ):
        raise AdminError(422, "InvalidExport", "Provide a scope and optional format=ndjson or zip")
    if values.get("format") in {"ndjson", "zip"}:
        # Validate before committing headers, then stream with the same scope lease.
        session = request.app.state.database.sessions()
        try:
            await session.begin()
            from mockazure.workloads.repository import scope_row

            if await scope_row(session, tenant_id, workload, values["scope"], lock="read") is None:
                raise AdminError(404, "ScopeNotFound", "Scope was not found")

            zipped = values.get("format") == "zip"
            if zipped:
                await check_archive_size(session, tenant_id, workload, values["scope"])

            async def body():
                try:
                    if zipped:
                        async for chunk in export_archive(
                            session, tenant_id, workload, values["scope"]
                        ):
                            yield chunk
                        return
                    async for row in export_records(session, tenant_id, workload, values["scope"]):
                        yield json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n"
                finally:
                    await session.close()

            from starlette.background import BackgroundTask

            return StreamingResponse(
                body(),
                media_type="application/zip" if zipped else "application/x-ndjson",
                background=BackgroundTask(session.close),
            )
        except BaseException:
            await session.close()
            raise
    async with tenant_session(request, tenant_id) as session:
        return await export_workload(session, tenant_id, workload, values["scope"])


class ResetInput(WorkloadInput):
    scope: str = Field(min_length=1, max_length=512)
    expected_revision: int = Field(ge=0, strict=True)


@router.post("/{workload}/reset")
async def reset(tenant_id: UUID, workload: str, request: Request):
    from mockazure.workloads.lifecycle import reset_workload

    try:
        body = ResetInput.model_validate(await read_json(request, limit=16384))
    except ValueError:
        raise AdminError(422, "InvalidReset", "Scope and expected revision are required") from None
    async with tenant_session(request, tenant_id) as session:
        return await reset_workload(
            session, tenant_id, workload, body.scope, body.expected_revision
        )


@router.get("/sharepoint/facts")
async def facts(tenant_id: UUID, request: Request):
    from sqlalchemy import select

    from mockazure.evidence.models import EvidenceSource

    pairs = list(request.query_params.multi_items())
    values = dict(pairs)
    if len(pairs) != len(values) or set(values) - {"scope", "after", "top"}:
        raise AdminError(422, "InvalidFactsQuery", "Unsupported fact query")
    try:
        top = int(values.get("top", "100"))
        after = int(values.get("after", "0"))
        if not 1 <= top <= 1000 or after < 0:
            raise ValueError()
    except ValueError:
        raise AdminError(422, "InvalidFactsQuery", "Invalid fact paging bounds") from None
    async with tenant_session(request, tenant_id) as session:
        query = select(EvidenceSource).where(
            EvidenceSource.tenant_id == tenant_id,
            EvidenceSource.kind == "workload",
            EvidenceSource.source_revision > after,
        )
        if "scope" in values:
            query = query.where(EvidenceSource.facts["scope"].as_string() == values["scope"])
        rows = list(
            await session.scalars(query.order_by(EvidenceSource.source_revision).limit(top + 1))
        )
        return {
            "value": [
                {
                    "id": str(row.id),
                    "revision": row.source_revision,
                    "occurred_at": row.occurred_at.isoformat(),
                    "facts": row.facts,
                }
                for row in rows[:top]
            ],
            "next_after": rows[top - 1].source_revision if len(rows) > top else None,
        }


class ConversationAccessInput(WorkloadInput):
    scope: str = Field(min_length=1, max_length=512)
    resource_kind: Literal["team", "channel", "chat"]
    resource_id: str = Field(min_length=1, max_length=512)
    principal_kind: Literal["user", "application"]
    principal_id: UUID
    permission: str = Field(min_length=1, max_length=64)


@router.post("/teams/access", status_code=201)
async def conversation_access(tenant_id: UUID, request: Request):
    from mockazure.workloads.team_access import set_access

    try:
        data = ConversationAccessInput.model_validate(await read_json(request, limit=16384))
    except ValueError:
        raise AdminError(422, "InvalidConsent", "Invalid conversation access record") from None
    async with tenant_session(request, tenant_id) as session:
        row = await set_access(session, tenant_id, data)
        return {"id": str(row.id), **data.model_dump(mode="json")}


@router.delete("/teams/access/{grant_id}", status_code=204)
async def remove_conversation_access(tenant_id: UUID, grant_id: UUID, request: Request):
    from mockazure.workloads.models import ConversationAccess
    from mockazure.workloads.repository import scope_row

    async with tenant_session(request, tenant_id) as session:
        row = await session.get(ConversationAccess, grant_id)
        if row is None or row.tenant_id != tenant_id:
            raise AdminError(404, "ConsentNotFound", "Conversation access record was not found")
        state = await scope_row(session, tenant_id, "teams", row.scope, lock=True)
        state.grant_revision += 1
        await session.delete(row)
    return Response(status_code=204)

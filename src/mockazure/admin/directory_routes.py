"""Authenticated, separate public-directory datasets and retention maintenance."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import anyio
from fastapi import APIRouter, Request
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from starlette.responses import StreamingResponse

from mockazure.admin.errors import AdminError
from mockazure.db.models import Tenant
from mockazure.directory.datasets import (
    MAX_BYTES,
    DirectoryDataset,
    export_directory,
    import_directory,
    validate_export,
)
from mockazure.directory.history import current_revision, lock_history, prune_history
from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/directory")


class SnapshotResponse(StreamingResponse):
    def __init__(self, content, *, session, **kwargs):
        super().__init__(content, **kwargs)
        self.session = session

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Own the transaction through header delivery too: the generator may never start.
            with anyio.CancelScope(shield=True):
                await self.session.close()


@router.post("/import", status_code=201)
async def import_dataset(tenant_id: UUID, request: Request, mode: Literal["create"] = "create"):
    try:
        raw = await read_json(request, limit=MAX_BYTES)
        dataset = DirectoryDataset.model_validate(raw)
    except GraphError as error:
        raise AdminError(error.status, "InvalidDataset", error.message) from None
    except ValidationError:
        raise AdminError(
            422, "InvalidDataset", "Dataset does not match the public schema"
        ) from None
    try:
        async with request.app.state.database.sessions.begin() as session:
            return await import_directory(session, tenant_id, dataset, mode)
    except IntegrityError:
        raise AdminError(
            409, "DatasetConflict", "Dataset IDs or identities already exist"
        ) from None


@router.get("/export")
async def export_dataset(tenant_id: UUID, request: Request):
    session = request.app.state.database.sessions()
    try:
        await session.begin()
        await lock_history(session, tenant_id)
        snapshot = await current_revision(session, tenant_id)
        await validate_export(session, tenant_id, snapshot)
    except BaseException:
        with anyio.CancelScope(shield=True):
            await session.close()
        raise

    return SnapshotResponse(
        export_directory(session, tenant_id, snapshot),
        session=session,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="directory.json"'},
    )


@router.post("/cleanup")
async def cleanup(tenant_id: UUID, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        if await session.get(Tenant, tenant_id) is None:
            raise AdminError(404, "TenantNotFound", "Tenant not found")
        return await prune_history(session, tenant_id, datetime.now(UTC))

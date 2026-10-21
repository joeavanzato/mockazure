"""Shared population contract for the CLI and console."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import Field
from sqlalchemy import select

from mockazure.admin.errors import AdminError
from mockazure.admin.profile_routes import ProfileRoute
from mockazure.population.models import OperationReceipt, PopulationRun, PopulationStep
from mockazure.population.planner import RunRequest, preview
from mockazure.population.protection import unseal
from mockazure.population.repository import (
    cancel_run,
    export_manifest,
    resume_run,
    run_view,
    submit_run,
)
from mockazure.population.types import Input
from mockazure.providers.graph.body import read_json

router = APIRouter(prefix="/_mock/population", route_class=ProfileRoute)


async def require_run_tenant(request, run_id, tenant_id):
    if tenant_id is not None:
        value = await run_view(request.app.state.database, run_id)
        if value.tenant_id != tenant_id:
            raise AdminError(404, "RunNotFound", "Run does not belong to the selected tenant")


@router.post("/previews", status_code=201)
async def create_preview(request: Request):
    value = RunRequest.model_validate(await read_json(request, limit=1024 * 1024))
    return await preview(request.app.state.database, value, datetime.now(UTC))


class Submission(Input):
    preview_id: UUID
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("/runs", status_code=201)
async def create_run(value: Submission, request: Request):
    return await submit_run(
        request.app.state.database, value.preview_id, value.digest, datetime.now(UTC)
    )


@router.get("/runs")
async def list_runs(
    request: Request,
    tenant_id: UUID | None = None,
    after: UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    database = request.app.state.database
    async with database.sessions() as session:
        query = select(PopulationRun.id).order_by(PopulationRun.id).limit(limit + 1)
        if tenant_id:
            query = query.where(PopulationRun.tenant_id == tenant_id)
        if after:
            query = query.where(PopulationRun.id > after)
        rows = list(await session.scalars(query))
    return {
        "items": [await run_view(database, identifier) for identifier in rows[:limit]],
        "next_after": rows[limit - 1] if len(rows) > limit else None,
    }


@router.get("/runs/{run_id}")
async def status(run_id: UUID, request: Request, tenant_id: UUID | None = None):
    await require_run_tenant(request, run_id, tenant_id)
    return await run_view(request.app.state.database, run_id)


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: UUID, request: Request, tenant_id: UUID | None = None):
    await require_run_tenant(request, run_id, tenant_id)
    return await cancel_run(request.app.state.database, run_id)


@router.post("/runs/{run_id}/resume")
async def resume(run_id: UUID, request: Request, tenant_id: UUID | None = None):
    await require_run_tenant(request, run_id, tenant_id)
    return await resume_run(request.app.state.database, run_id)


@router.get("/runs/{run_id}/manifest")
async def manifest(run_id: UUID, request: Request, tenant_id: UUID | None = None):
    await require_run_tenant(request, run_id, tenant_id)
    return await export_manifest(request.app.state.database, run_id)


@router.get("/runs/{run_id}/steps")
async def steps(
    run_id: UUID,
    request: Request,
    tenant_id: UUID | None = None,
    after: int = Query(-1, ge=-1),
    limit: int = Query(100, ge=1, le=500),
):
    await require_run_tenant(request, run_id, tenant_id)
    database = request.app.state.database
    await run_view(database, run_id)
    async with database.sessions() as session:
        rows = list(
            await session.scalars(
                select(PopulationStep)
                .where(PopulationStep.run_id == run_id, PopulationStep.ordinal > after)
                .order_by(PopulationStep.ordinal)
                .limit(limit + 1)
            )
        )
        return {
            "items": [
                {
                    k: getattr(row, k)
                    for k in ("ordinal", "key", "family", "count", "offset", "state")
                }
                for row in rows[:limit]
            ],
            "next_after": rows[limit - 1].ordinal if len(rows) > limit else None,
        }


@router.get("/runs/{run_id}/ground-truth")
async def ground_truth(
    run_id: UUID,
    request: Request,
    tenant_id: UUID | None = None,
    after: str | None = Query(None, max_length=64),
    limit: int = Query(10, ge=1, le=10),
):
    await require_run_tenant(request, run_id, tenant_id)
    database = request.app.state.database
    await run_view(database, run_id)
    async with database.sessions() as session:
        run = await session.get(PopulationRun, run_id)
        query = (
            select(OperationReceipt)
            .where(
                OperationReceipt.tenant_id == run.tenant_id,
                OperationReceipt.namespace == run.namespace,
                OperationReceipt.state == "verified",
            )
            .order_by(OperationReceipt.key)
            .limit(limit + 1)
        )
        if after:
            query = query.where(OperationReceipt.key > after)
        rows = list(await session.scalars(query))
        items = [
            {
                "family": row.family,
                "operation_key": row.key,
                "records": [
                    {
                        "id": item["id"],
                        "parent": item.get("parent"),
                        **(
                            {"provenance": item["properties"]["provenance"]}
                            if row.family.startswith("hunting.")
                            and item.get("properties", {}).get("provenance")
                            else {}
                        ),
                    }
                    for item in unseal(database, row.encrypted_result)["items"]
                ],
            }
            for row in rows[:limit]
        ]
        return {
            "namespace": run.namespace,
            "scope": "verified namespace receipts",
            "items": items,
            "next_after": rows[limit - 1].key if len(rows) > limit else None,
        }

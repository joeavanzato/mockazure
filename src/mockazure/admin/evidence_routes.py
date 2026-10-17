from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field, ValidationError
from sqlalchemy import select
from starlette.responses import StreamingResponse

from mockazure.admin.errors import AdminError
from mockazure.db.job_model import Job
from mockazure.db.models import Tenant
from mockazure.evidence.cleanup import cleanup_evidence, reset_evidence
from mockazure.evidence.clock import evidence_now, update_settings
from mockazure.evidence.datasets import (
    EvidenceDataset,
    EvidenceExportQuery,
    export_evidence,
    import_evidence,
)
from mockazure.evidence.models import EvidenceCursor
from mockazure.evidence.operations import drain_evidence, job_view, retry_projection
from mockazure.evidence.repository import lock_state
from mockazure.evidence.risk import record_risk_observation
from mockazure.evidence.schemas import EvidenceSettings, RiskObservation, StrictModel
from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.evidence.paging import issue_cursor, snapshot_evidence

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/evidence")


@asynccontextmanager
async def tenant_session(request, tenant_id):
    async with request.app.state.database.sessions.begin() as session:
        if await session.get(Tenant, tenant_id) is None:
            raise AdminError(404, "TenantNotFound", "Tenant not found")
        yield session


async def read_model(request, model, limit=1024 * 1024):
    try:
        return model.model_validate(await read_json(request, limit))
    except (ValueError, ValidationError):
        raise AdminError(
            422, "InvalidEvidenceInput", "Input is outside the evidence schema"
        ) from None


class Advance(StrictModel):
    seconds: int = Field(ge=0, le=31536000, strict=True)


class Drain(StrictModel):
    max_jobs: int = Field(default=100, ge=1, le=1000, strict=True)


class Cleanup(StrictModel):
    max_rows: int = Field(default=500, ge=1, le=1000, strict=True)


class Reset(StrictModel):
    expected_generation: UUID


@router.get("/state")
async def state(tenant_id: UUID, request: Request):
    async with tenant_session(request, tenant_id) as session:
        row = await lock_state(session, tenant_id)
        return {
            "generation": str(row.generation),
            "source_revision": row.source_revision,
            "publication_revision": row.publication_revision,
            "evidence_time": (
                await evidence_now(session, tenant_id, datetime.now(UTC))
            ).isoformat(),
        }


@router.get("/settings")
async def settings(tenant_id: UUID, request: Request):
    async with tenant_session(request, tenant_id) as session:
        row = await lock_state(session, tenant_id)
        return EvidenceSettings.model_validate(row.settings)


@router.patch("/settings")
async def change_settings(tenant_id: UUID, request: Request):
    changes = await read_json(request)
    async with tenant_session(request, tenant_id) as session:
        row = await lock_state(session, tenant_id)
        try:
            value = EvidenceSettings.model_validate({**row.settings, **changes})
            return await update_settings(session, tenant_id, value)
        except ValueError:
            raise AdminError(
                422, "InvalidEvidenceSettings", "Invalid evidence settings or clock rewind"
            ) from None


@router.post("/clock/advance")
async def advance(tenant_id: UUID, request: Request):
    value = await read_model(request, Advance)
    async with tenant_session(request, tenant_id) as session:
        row = await lock_state(session, tenant_id)
        previous = EvidenceSettings.model_validate(row.settings)
        if previous.clock_mode != "fixed":
            raise AdminError(
                409, "EvidenceClockNotFixed", "Only a fixed evidence clock can advance"
            )
        try:
            updated = previous.model_copy(
                update={"fixed_time": previous.fixed_time + timedelta(seconds=value.seconds)}
            )
            await update_settings(session, tenant_id, updated)
        except (ValueError, OverflowError):
            raise AdminError(
                422, "InvalidEvidenceClock", "Evidence time is outside the supported range"
            ) from None
        return {"evidence_time": updated.fixed_time.isoformat()}


@router.post("/drain")
async def drain(tenant_id: UUID, request: Request):
    value = await read_model(request, Drain)
    async with tenant_session(request, tenant_id):
        pass
    return await drain_evidence(request.app.state.database, tenant_id, value.max_jobs)


@router.post("/cleanup")
async def cleanup(tenant_id: UUID, request: Request):
    value = await read_model(request, Cleanup)
    async with tenant_session(request, tenant_id) as session:
        return await cleanup_evidence(session, tenant_id, datetime.now(UTC), value.max_rows)


@router.post("/reset")
async def reset(tenant_id: UUID, request: Request):
    value = await read_model(request, Reset)
    async with tenant_session(request, tenant_id) as session:
        return {
            "generation": str(await reset_evidence(session, tenant_id, value.expected_generation))
        }


@router.post("/import")
async def import_records(tenant_id: UUID, request: Request):
    value = await read_model(request, EvidenceDataset, 10 * 1024 * 1024)
    async with tenant_session(request, tenant_id) as session:
        return await import_evidence(session, tenant_id, value)


@router.get("/export")
async def export_records(tenant_id: UUID, request: Request):
    values = dict(request.query_params)
    if len(values) != len(request.query_params.multi_items()) or values.keys() - {
        "family",
        "from",
        "to",
    }:
        raise AdminError(422, "InvalidEvidenceQuery", "Unsupported export query")
    try:
        value = EvidenceExportQuery(
            family=values.get("family"), from_time=values.get("from"), to_time=values.get("to")
        )
    except ValueError:
        raise AdminError(422, "InvalidEvidenceQuery", "Invalid export range or family") from None
    async with tenant_session(request, tenant_id):
        pass
    return StreamingResponse(
        export_evidence(request.app.state.database.sessions, tenant_id, value),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="evidence.json"'},
    )


@router.post("/risk-observations")
async def risk_observation(tenant_id: UUID, request: Request):
    value = await read_model(request, RiskObservation)
    async with tenant_session(request, tenant_id) as session:
        return await record_risk_observation(session, tenant_id, value)


@router.post("/jobs/{job_id}/retry")
async def retry(tenant_id: UUID, job_id: UUID, request: Request):
    if await request.body():
        raise AdminError(422, "InvalidEvidenceInput", "Retry does not accept a body")
    async with tenant_session(request, tenant_id) as session:
        return await retry_projection(session, tenant_id, job_id, datetime.now(UTC))


@router.get("/jobs")
async def jobs(tenant_id: UUID, request: Request):
    import hashlib
    import re

    values = dict(request.query_params)
    if len(values) != len(request.query_params.multi_items()) or values.keys() - {
        "state",
        "limit",
        "continuation",
    }:
        raise AdminError(422, "InvalidEvidenceQuery", "Unsupported jobs query")
    wall = datetime.now(UTC)
    token = values.get("continuation")
    async with tenant_session(request, tenant_id) as session:
        current = await lock_state(session, tenant_id)
        if token:
            if len(values) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
                raise AdminError(400, "InvalidContinuation", "Invalid jobs continuation")
            row = await session.get(EvidenceCursor, hashlib.sha256(token.encode()).hexdigest())
            if (
                row is None
                or row.tenant_id != tenant_id
                or row.expires_at <= wall
                or row.generation != current.generation
                or row.state.get("kind") != "jobs"
            ):
                raise AdminError(400, "InvalidContinuation", "Jobs continuation is unavailable")
            data = row.state
            from mockazure.providers.graph.evidence.paging import EvidenceSnapshot

            snapshot = EvidenceSnapshot(
                row.generation,
                row.revision,
                row.evidence_time,
                row.retention_cutoff,
                row.expires_at,
            )
        else:
            text = values.get("limit", "50")
            if (
                not text.isascii()
                or not text.isdigit()
                or len(text) > 3
                or not 1 <= int(text) <= 100
            ):
                raise AdminError(422, "InvalidEvidenceQuery", "Jobs limit must be 1..100")
            if values.get("state") not in {None, "pending", "running", "succeeded", "failed"}:
                raise AdminError(422, "InvalidEvidenceQuery", "Unsupported job state")
            snapshot = await snapshot_evidence(session, tenant_id, wall)
            data = {
                "kind": "jobs",
                "filter": values.get("state"),
                "limit": int(text),
                "created_before": wall.isoformat(),
                "last": None,
            }
        conditions = [
            Job.tenant_id == tenant_id,
            Job.kind == "evidence.project",
            Job.payload["generation"].as_string() == str(current.generation),
            Job.created_at <= datetime.fromisoformat(data["created_before"]),
        ]
        if data["filter"]:
            conditions.append(Job.state == data["filter"])
        if data["last"]:
            conditions.append(Job.id > UUID(data["last"]))
        rows = list(
            await session.scalars(
                select(Job).where(*conditions).order_by(Job.id).limit(data["limit"] + 1)
            )
        )
        next_token = None
        if len(rows) > data["limit"]:
            next_data = {**data, "last": str(rows[data["limit"] - 1].id)}
            next_token = await issue_cursor(session, snapshot, tenant_id, next_data, token)
        return {"value": [job_view(row) for row in rows[: data["limit"]]], "next": next_token}

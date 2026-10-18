"""Local-only feed fixtures, fault controls and bounded lifecycle operations."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import AwareDatetime, Field
from sqlalchemy import delete, func, select

from mockazure.activity.models import Content, Cursor, Quota, Subscription
from mockazure.activity.publication import publish_activity, store_records
from mockazure.activity.subscriptions import state
from mockazure.admin.errors import AdminError
from mockazure.admin.evidence_routes import read_model, tenant_session
from mockazure.db.job_model import Job
from mockazure.evidence.clock import evidence_now
from mockazure.evidence.schemas import StrictModel
from mockazure.jobs.dispatch import dispatch_job
from mockazure.jobs.repository import claim_job
from mockazure.providers.graph.body import read_json

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/activity")


class Settings(StrictModel):
    delay_seconds: int = Field(default=0, ge=0, le=86400, strict=True)
    fail_attempts: int = Field(default=0, ge=0, le=2, strict=True)
    page_size: int = Field(default=100, ge=1, le=1000, strict=True)
    request_limit: int = Field(default=2000, ge=1, le=100000, strict=True)
    window_seconds: int = Field(default=60, ge=1, le=3600, strict=True)


class Import(StrictModel):
    records: list[dict] = Field(min_length=1, max_length=1000)


class Publish(StrictModel):
    record_ids: list[UUID] = Field(min_length=1, max_length=1000)


class Reset(StrictModel):
    expected_generation: UUID
    clear_settings: bool = Field(default=False, strict=True)


class Bounded(StrictModel):
    max_rows: int = Field(default=100, ge=1, le=1000, strict=True)


class ArchivedContent(StrictModel):
    content_id: str = Field(max_length=64)
    published_at: AwareDatetime
    records: list[dict] = Field(min_length=1, max_length=1000)


class Manifest(StrictModel):
    version: int = Field(default=1, ge=1, le=1, strict=True)
    tenant_id: UUID
    settings: Settings
    contents: list[ArchivedContent] = Field(max_length=1000)
    next_revision: int | None = None


@router.get("/state")
async def get_state(tenant_id: UUID, request: Request):
    async with tenant_session(request, tenant_id) as session:
        current = await state(session, tenant_id)
        counts = dict(
            (
                await session.execute(
                    select(Job.state, func.count())
                    .where(Job.tenant_id == tenant_id, Job.kind == "activity.publish")
                    .group_by(Job.state)
                )
            ).all()
        )
        return {
            "jobs": counts,
            "generation": str(current.generation),
            "revision": current.revision,
            "settings": current.settings,
        }


@router.patch("/settings")
async def settings(tenant_id: UUID, request: Request):
    changes = await read_json(request)
    async with tenant_session(request, tenant_id) as session:
        current = await state(session, tenant_id, lock=True)
        try:
            if not isinstance(changes, dict):
                raise ValueError()
            value = Settings.model_validate({**current.settings, **changes})
        except ValueError:
            raise AdminError(422, "InvalidActivitySettings", "Unsupported feed settings") from None
        current.settings = value.model_dump()
        return current.settings


async def add_records(tenant_id, request, value, canonical):
    async with tenant_session(request, tenant_id) as session:
        now = await evidence_now(session, tenant_id, datetime.now(UTC))
        try:
            identifiers = (
                await publish_activity(session, tenant_id, value.record_ids, now)
                if canonical
                else await store_records(session, tenant_id, value.records, now)
            )
        except (ValueError, TypeError, KeyError, OverflowError):
            raise AdminError(
                422, "InvalidActivityRecord", "Invalid, conflicting or unavailable audit record"
            ) from None
        return {"content_ids": identifiers}


@router.post("/import")
async def import_records(tenant_id: UUID, request: Request):
    return await add_records(
        tenant_id, request, await read_model(request, Import, 10 * 1024 * 1024), False
    )


@router.post("/publish")
async def publish(tenant_id: UUID, request: Request):
    return await add_records(tenant_id, request, await read_model(request, Publish), True)


@router.get("/export")
async def export(tenant_id: UUID, request: Request):
    params = dict(request.query_params)
    try:
        if params.keys() - {"after_revision"} or len(params) != len(
            request.query_params.multi_items()
        ):
            raise ValueError()
        after = int(params.get("after_revision", "0"))
        if after < 0 or after >= 2**63:
            raise ValueError()
    except ValueError:
        raise AdminError(422, "InvalidActivityExport", "Invalid export continuation") from None
    async with tenant_session(request, tenant_id) as session:
        current = await state(session, tenant_id)
        # Bounded pages; callers concatenate contents, not subscription authorities or tokens.
        rows = await session.stream_scalars(
            select(Content)
            .where(Content.tenant_id == tenant_id, Content.revision > after, Content.records != [])
            .order_by(Content.revision)
            .limit(1001)
            .execution_options(yield_per=1)
        )
        contents, size, last, more = [], 0, after, False
        async for row in rows:
            item = {
                "content_id": row.id,
                "published_at": row.published_at.isoformat(),
                "records": row.records,
            }
            width = len(json.dumps(item).encode())
            if contents and (len(contents) >= 1000 or size + width > 8 * 1024 * 1024):
                more = True
                break
            contents.append(item)
            size += width
            last = row.revision
        return {
            "version": 1,
            "tenant_id": str(tenant_id),
            "settings": Settings.model_validate(current.settings).model_dump(),
            "contents": contents,
            "next_revision": last if more else None,
        }


@router.post("/restore")
async def restore(tenant_id: UUID, request: Request):
    manifest = await read_model(request, Manifest, 10 * 1024 * 1024)
    if manifest.tenant_id != tenant_id or sum(len(c.records) for c in manifest.contents) > 1000:
        raise AdminError(422, "InvalidActivityArchive", "Tenant or record count mismatch")
    async with tenant_session(request, tenant_id) as session:
        now = await evidence_now(session, tenant_id, datetime.now(UTC))
        current = await state(session, tenant_id, lock=True)
        count = 0
        try:
            for item in manifest.contents:
                if item.published_at > now:
                    raise ValueError()
                count += len(
                    await store_records(
                        session, tenant_id, item.records, item.published_at, current=current
                    )
                )
        except (ValueError, TypeError, KeyError, OverflowError):
            raise AdminError(
                422, "InvalidActivityArchive", "Invalid or conflicting archived content"
            ) from None
        current.settings = manifest.settings.model_dump()
        return {"restored": count}


@router.post("/reset")
async def reset(tenant_id: UUID, request: Request):
    value = await read_model(request, Reset)
    async with tenant_session(request, tenant_id) as session:
        current = await state(session, tenant_id, lock=True)
        if current.generation != value.expected_generation:
            raise AdminError(409, "ActivityGenerationConflict", "Feed generation changed")
        for model in (Cursor, Subscription, Quota, Content):
            await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await session.execute(
            delete(Job).where(Job.tenant_id == tenant_id, Job.kind == "activity.publish")
        )
        current.generation, current.revision = uuid4(), 0
        if value.clear_settings:
            current.settings = {}
        return {"generation": str(current.generation)}


@router.post("/cleanup")
async def cleanup(tenant_id: UUID, request: Request):
    value = await read_model(request, Bounded)
    async with tenant_session(request, tenant_id) as session:
        now = await evidence_now(session, tenant_id, datetime.now(UTC))
        await state(session, tenant_id, lock=True)
        rows = list(
            await session.scalars(
                select(Content)
                .where(
                    Content.tenant_id == tenant_id,
                    Content.published_at <= now - timedelta(days=7),
                    Content.records != [],
                )
                .order_by(Content.revision)
                .limit(value.max_rows)
            )
        )
        for row in rows:
            # Keep the identity receipt so retries cannot resurrect expired content.
            row.records = []
        cursors = list(
            await session.scalars(
                select(Cursor.token_hash)
                .where(Cursor.tenant_id == tenant_id, Cursor.expires_at <= datetime.now(UTC))
                .limit(value.max_rows)
            )
        )
        if cursors:
            await session.execute(delete(Cursor).where(Cursor.token_hash.in_(cursors)))
        return {"pruned_contents": len(rows), "deleted_cursors": len(cursors)}


@router.post("/drain")
async def drain(tenant_id: UUID, request: Request):
    value = await read_model(request, Bounded)
    async with tenant_session(request, tenant_id):
        pass
    results = []
    for _ in range(value.max_rows):
        now = datetime.now(UTC)
        async with request.app.state.database.sessions.begin() as session:
            claim = await claim_job(
                session,
                owner=uuid4(),
                now=now,
                lease_seconds=60,
                tenant_id=tenant_id,
                kind="activity.publish",
            )
        if claim is None:
            break
        result = await dispatch_job(request.app.state.database, claim, now)
        results.append(
            {"id": str(claim.id), "state": result.state, "error_code": result.error_code}
        )
    return {"processed": len(results), "jobs": results}

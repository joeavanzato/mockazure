"""Administrative worker operations stay tenant-scoped and use the normal fenced dispatcher."""

import hashlib
import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from mockazure.admin.errors import AdminError
from mockazure.db.job_model import Job
from mockazure.evidence.repository import lock_state
from mockazure.evidence.schemas import OperationEvidenceContract
from mockazure.jobs.dispatch import dispatch_job
from mockazure.jobs.repository import claim_job

MAPPING_VERSION = "business-evidence-v1"
DATA = Path(__file__).parent / "data"


@lru_cache(maxsize=1)
def _mappings():
    from mockazure.living_tenant.contracts import list_operations, read_manifest

    value = json.loads((DATA / "operation_mappings.json").read_text("utf-8"))
    if set(value) != {"version", "sources", "operations"} or value["version"] != MAPPING_VERSION:
        raise ValueError("Unsupported evidence mapping version")
    for source in value["sources"].values():
        path = (DATA / source["path"]).resolve()
        if not path.is_relative_to(DATA.resolve()) or (
            hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]
        ):
            raise ValueError("Evidence source hash mismatch")
    manifest = read_manifest()
    refs = (
        set(value["sources"])
        | set(manifest["sources"])
        | {f"catalog:{key}" for key in manifest.get("catalogs", {})}
    )
    rows = [OperationEvidenceContract.model_validate(row) for row in value["operations"]]
    result = {row.operation_key: row for row in rows}
    if len(result) != len(rows) or any(not set(row.schema_refs) <= refs for row in rows):
        raise ValueError("Duplicate mapping or missing evidence source reference")
    if not {row.key for row in list_operations()} <= result.keys():
        raise ValueError("Operation manifest lacks explicit evidence dispositions")
    return result


def mapping(operation_key: str) -> OperationEvidenceContract:
    if operation_key not in _mappings():
        raise ValueError("Unknown evidence operation")
    return _mappings()[operation_key]


def verify_operation_mappings() -> int:
    _mappings.cache_clear()
    return len(_mappings())


def job_view(row):
    source = row.payload.get("source", {})
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "kind": row.kind,
        "state": row.state,
        "attempts": row.attempts,
        "error_code": row.error_code,
        "source_kind": source.get("kind"),
        "source_id": source.get("id"),
        "generation": row.payload.get("generation"),
    }


async def retry_projection(session, tenant_id, job_id, wall_now):
    state = await lock_state(session, tenant_id)
    row = await session.scalar(
        select(Job)
        .where(Job.tenant_id == tenant_id, Job.id == job_id, Job.kind == "evidence.project")
        .with_for_update()
    )
    if row is None:
        raise AdminError(404, "JobNotFound", "Projection job not found")
    if row.state != "failed" or row.payload.get("generation") != str(state.generation):
        raise AdminError(
            409, "JobNotRetryable", "Only a failed job in the current generation can retry"
        )
    row.state, row.attempts, row.error_code = "pending", 0, None
    row.claim_epoch += 1
    row.available_at, row.updated_at = wall_now, wall_now
    row.lease_owner = row.lease_until = None
    await session.flush()
    return job_view(row)


async def drain_evidence(database, tenant_id, max_jobs=100):
    from mockazure.evidence.sources import scan_sources

    if not 1 <= max_jobs <= 1000:
        raise ValueError("Drain limit must be 1..1000")
    processed = 0
    for _ in range(max_jobs):
        async with database.sessions.begin() as session:
            await scan_sources(session, limit=100, tenant_id=tenant_id)
        async with database.sessions.begin() as session:
            claim = await claim_job(
                session,
                owner=uuid4(),
                now=datetime.now(UTC),
                lease_seconds=30,
                tenant_id=tenant_id,
                kind="evidence.project",
            )
        if claim is None:
            break
        await dispatch_job(database, claim, datetime.now(UTC))
        processed += 1
    async with database.sessions.begin() as session:
        state = await lock_state(session, tenant_id)
        counts = dict(
            (
                await session.execute(
                    select(Job.state, func.count())
                    .where(
                        Job.tenant_id == tenant_id,
                        Job.kind == "evidence.project",
                        Job.payload["generation"].as_string() == str(state.generation),
                    )
                    .group_by(Job.state)
                )
            ).all()
        )
    return {
        "processed": processed,
        "pending": counts.get("pending", 0) + counts.get("running", 0),
        "failed": counts.get("failed", 0),
    }

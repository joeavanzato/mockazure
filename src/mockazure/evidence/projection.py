"""Publish and acknowledge under one tenant-generation and job-lease fence."""

from datetime import datetime, timedelta
from time import monotonic
from uuid import UUID

from sqlalchemy import select

from mockazure.db.engine import Database
from mockazure.db.job_model import Job
from mockazure.evidence.projectors import project_source
from mockazure.evidence.repository import load_source, lock_state, publish_batch
from mockazure.evidence.schemas import SourceRef
from mockazure.jobs.repository import fenced
from mockazure.jobs.schemas import JobClaim, JobResult


class LeaseExpired(Exception):
    pass


async def process_projection(
    database: Database,
    claim: JobClaim,
    wall_now: datetime,
) -> JobResult:
    started = monotonic()

    def now():
        return wall_now + timedelta(seconds=monotonic() - started)

    try:
        async with database.sessions.begin() as session:
            if set(claim.payload) != {"source", "generation"}:
                raise ValueError("Invalid projection job")
            ref = SourceRef.model_validate(claim.payload["source"])
            if claim.kind != "evidence.project" or ref.tenant_id != claim.tenant_id:
                raise ValueError("Projection job identity mismatch")
            generation = UUID(claim.payload["generation"])
            source = await load_source(session, ref)
            if source is None:
                raise ValueError("Missing source")
            projected = project_source(source)
            state = await lock_state(session, ref.tenant_id)
            row = await session.scalar(select(Job).where(*fenced(claim, now())).with_for_update())
            if row is None or row.lease_until <= now():
                return JobResult("stale", None, None)
            if generation != state.generation or source.generation != state.generation:
                row.state, row.error_code = "failed", "StaleEvidenceGeneration"
                row.lease_owner = row.lease_until = None
                return JobResult("stale", None, "StaleEvidenceGeneration")
            result = await publish_batch(session, projected)
            if row.lease_until <= now():
                raise LeaseExpired()
            row.state, row.result, row.error_code = (
                "succeeded",
                result.model_dump(mode="json"),
                None,
            )
            row.lease_owner = row.lease_until = None
            row.updated_at = now()
            await session.flush()
            return JobResult("succeeded", row.result, None)
    except LeaseExpired:
        # Roll back observations/receipts/acknowledgement as well as completion.
        return JobResult("stale", None, None)

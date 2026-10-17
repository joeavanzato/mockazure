"""Atomic SQL materialization with the shared epoch/lease fence."""

from datetime import timedelta
from time import monotonic
from uuid import UUID

from sqlalchemy import delete, func, insert, literal, select

from mockazure.auditlog.models import AuditQuery, AuditQueryItem
from mockazure.auditlog.queries import pin_key, source_predicates
from mockazure.db.job_model import Job
from mockazure.evidence.models import EvidenceCursor, EvidenceVersion
from mockazure.evidence.projection import LeaseExpired
from mockazure.evidence.repository import lock_state
from mockazure.jobs.repository import fenced
from mockazure.jobs.schemas import JobResult


async def process_audit_query(database, claim, wall_now):
    started = monotonic()

    def now():
        return wall_now + timedelta(seconds=monotonic() - started)

    try:
        async with database.sessions.begin() as session:
            if claim.kind != "audit.query" or set(claim.payload) != {"query_id"}:
                raise ValueError("Invalid audit query job")
            identifier = UUID(claim.payload["query_id"])
            await lock_state(session, claim.tenant_id)
            job = await session.scalar(select(Job).where(*fenced(claim, now())).with_for_update())
            if job is None or job.lease_until <= now():
                return JobResult("stale", None, None)
            row = await session.scalar(
                select(AuditQuery)
                .where(
                    AuditQuery.tenant_id == claim.tenant_id,
                    AuditQuery.id == identifier,
                    AuditQuery.job_id == job.id,
                )
                .with_for_update()
            )
            if row is None:
                raise ValueError("Audit query identity mismatch")
            if row.expires_at <= now():
                job.state, job.error_code = "failed", "AuditQueryExpired"
            else:
                await session.execute(
                    insert(AuditQueryItem).from_select(
                        ["tenant_id", "query_id", "record_id", "event_time", "payload"],
                        select(
                            EvidenceVersion.tenant_id,
                            literal(row.id),
                            EvidenceVersion.record_id,
                            EvidenceVersion.event_time,
                            EvidenceVersion.payload,
                        ).where(*source_predicates(row)),
                    )
                )
                row.record_count = await session.scalar(
                    select(func.count())
                    .select_from(AuditQueryItem)
                    .where(
                        AuditQueryItem.tenant_id == row.tenant_id,
                        AuditQueryItem.query_id == row.id,
                    )
                )
                job.state, job.result, job.error_code = (
                    "succeeded",
                    {"record_count": row.record_count},
                    None,
                )
            if job.lease_until <= now():
                raise LeaseExpired()
            await session.execute(
                delete(EvidenceCursor).where(EvidenceCursor.token_hash == pin_key(row.id))
            )
            job.lease_owner = job.lease_until = None
            job.updated_at = now()
            await session.flush()
            return JobResult(job.state, job.result, job.error_code)
    except LeaseExpired:
        return JobResult("stale", None, None)

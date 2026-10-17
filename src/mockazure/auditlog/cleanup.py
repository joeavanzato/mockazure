"""Bounded result reclamation keeps query tombstones and fences expired workers."""

from sqlalchemy import String, delete, exists, select, tuple_

from mockazure.auditlog.models import AuditQuery, AuditQueryItem
from mockazure.auditlog.queries import pin_key
from mockazure.db.job_model import Job
from mockazure.evidence.models import EvidenceCursor
from mockazure.evidence.repository import lock_state


async def cleanup_audit(session, tenant_id, now, limit):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Cleanup limit must be 1..1000")
    await lock_state(session, tenant_id)
    expired = select(AuditQuery.id).where(
        AuditQuery.tenant_id == tenant_id, AuditQuery.expires_at <= now
    )
    jobs = list(
        await session.scalars(
            select(Job)
            .join(AuditQuery, AuditQuery.job_id == Job.id)
            .where(
                Job.tenant_id == tenant_id,
                AuditQuery.tenant_id == tenant_id,
                AuditQuery.expires_at <= now,
                Job.state.in_(["pending", "running"]),
            )
            .order_by(Job.id)
            .limit(limit)
            .with_for_update(of=Job, skip_locked=True)
        )
    )
    for job in jobs:
        job.state, job.error_code = "failed", "AuditQueryExpired"
        job.lease_owner = job.lease_until = None
        job.claim_epoch += 1
        job.updated_at = now
    await session.flush()
    # Query identity stays available for its existing authenticated 410 response.
    # A pin can be released only after its job has been fenced or completed.
    queries = list(
        await session.scalars(
            select(AuditQuery.id)
            .join(Job, Job.id == AuditQuery.job_id)
            .where(
                AuditQuery.tenant_id == tenant_id,
                AuditQuery.expires_at <= now,
                Job.state.in_(["succeeded", "failed"]),
                exists(
                    select(1).where(
                        EvidenceCursor.tenant_id == tenant_id,
                        EvidenceCursor.state["query_id"].astext == AuditQuery.id.cast(String),
                        EvidenceCursor.state["kind"].astext == "audit.query",
                    )
                ),
            )
            .order_by(AuditQuery.id)
            .limit(limit)
        )
    )
    pins = 0
    if queries:
        result = await session.execute(
            delete(EvidenceCursor).where(
                EvidenceCursor.tenant_id == tenant_id,
                EvidenceCursor.token_hash.in_([pin_key(identifier) for identifier in queries]),
            )
        )
        pins = result.rowcount
    remaining = limit - pins
    items = (
        (
            await session.execute(
                select(AuditQueryItem.query_id, AuditQueryItem.record_id)
                .where(
                    AuditQueryItem.tenant_id == tenant_id,
                    AuditQueryItem.query_id.in_(expired),
                )
                .order_by(AuditQueryItem.query_id, AuditQueryItem.record_id)
                .limit(remaining)
            )
        ).all()
        if remaining
        else []
    )
    if items:
        await session.execute(
            delete(AuditQueryItem).where(
                AuditQueryItem.tenant_id == tenant_id,
                tuple_(AuditQueryItem.query_id, AuditQueryItem.record_id).in_(items),
            )
        )
    more_items = await session.scalar(
        select(
            exists(
                select(1).where(
                    AuditQueryItem.tenant_id == tenant_id, AuditQueryItem.query_id.in_(expired)
                )
            )
        )
    )
    more_jobs = await session.scalar(
        select(
            exists(
                select(1)
                .select_from(Job)
                .join(AuditQuery, AuditQuery.job_id == Job.id)
                .where(
                    AuditQuery.tenant_id == tenant_id,
                    AuditQuery.expires_at <= now,
                    Job.state.in_(["pending", "running"]),
                )
            )
        )
    )
    # Pins can remain if the deletion budget was exhausted or another worker held a job.
    more_pins = await session.scalar(
        select(
            exists(
                select(1)
                .select_from(EvidenceCursor)
                .join(
                    AuditQuery,
                    EvidenceCursor.state["query_id"].astext == AuditQuery.id.cast(String),
                )
                .where(
                    EvidenceCursor.tenant_id == tenant_id,
                    AuditQuery.tenant_id == tenant_id,
                    EvidenceCursor.state["kind"].astext == "audit.query",
                    AuditQuery.expires_at <= now,
                )
            )
        )
    )
    return {
        "deleted": pins + len(items),
        "items": len(items),
        "pins": pins,
        "fenced_jobs": len(jobs),
        "remaining": bool(more_items or more_jobs or more_pins),
    }

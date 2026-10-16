from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.db.job_model import Job
from mockazure.jobs.schemas import ClaimedJob, ExportSnapshot, JobView
from mockazure.tenants.repository import get_tenant
from mockazure.tenants.schemas import TenantCreate


class TenantNotFound(Exception):
    pass


async def enqueue_export(session: AsyncSession, tenant_id: UUID) -> JobView:
    tenant = await get_tenant(session, tenant_id)
    if tenant is None:
        raise TenantNotFound()
    snapshot = ExportSnapshot(
        source_tenant_id=tenant_id,
        captured_at=datetime.now(timezone.utc),
        tenant=TenantCreate.model_validate(
            tenant.model_dump(include=set(TenantCreate.model_fields))
        ),
    )
    job = Job(tenant_id=tenant_id, payload=snapshot.model_dump(mode="json"))
    session.add(job)
    await session.flush()
    return JobView.model_validate(job)


async def get_job(session: AsyncSession, tenant_id: UUID, job_id: UUID) -> JobView | None:
    row = await session.scalar(select(Job).where(Job.id == job_id, Job.tenant_id == tenant_id))
    return JobView.model_validate(row) if row else None


async def get_export_result(
    session: AsyncSession, tenant_id: UUID, job_id: UUID
) -> ExportSnapshot | None:
    result = await session.scalar(
        select(Job.result).where(
            Job.id == job_id, Job.tenant_id == tenant_id, Job.state == "succeeded"
        )
    )
    return ExportSnapshot.model_validate(result) if result else None


async def claim_job(
    session: AsyncSession, *, owner: UUID, now: datetime, lease_seconds: int
) -> ClaimedJob | None:
    if not 1 <= lease_seconds <= 3600:
        raise ValueError("invalid job lease")
    await session.execute(
        update(Job)
        .where(Job.state == "running", Job.attempts >= 3, Job.lease_until <= now)
        .values(
            state="failed",
            error_code="WorkerLeaseExpired",
            lease_owner=None,
            lease_until=None,
            updated_at=now,
        )
    )
    row = await session.scalar(
        select(Job)
        .where(
            Job.attempts < 3,
            or_(
                and_(Job.state == "pending", Job.available_at <= now),
                and_(Job.state == "running", Job.lease_until <= now),
            ),
        )
        .order_by(Job.available_at, Job.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if row is None:
        return None
    row.state, row.lease_owner = "running", owner
    row.attempts += 1
    row.lease_until = now + timedelta(seconds=lease_seconds)
    row.updated_at, row.error_code = now, None
    await session.flush()
    return ClaimedJob(row.id, owner, row.attempts, ExportSnapshot.model_validate(row.payload))


def fenced(claim):
    return (
        Job.id == claim.id,
        Job.state == "running",
        Job.lease_owner == claim.owner,
        Job.attempts == claim.attempt,
    )


async def finish_export(session: AsyncSession, claim: ClaimedJob, result: ExportSnapshot) -> bool:
    changed = await session.execute(
        update(Job)
        .where(*fenced(claim))
        .values(
            state="succeeded",
            result=result.model_dump(mode="json"),
            lease_owner=None,
            lease_until=None,
            error_code=None,
            updated_at=func.now(),
        )
    )
    return changed.rowcount == 1


async def fail_job(
    session: AsyncSession, claim: ClaimedJob, *, error_code: str, now: datetime
) -> bool:
    code = error_code if error_code in {"TransientError", "ExportFailed"} else "ExportFailed"
    changed = await session.execute(
        update(Job)
        .where(*fenced(claim))
        .values(
            state="failed" if claim.attempt >= 3 else "pending",
            error_code=code,
            available_at=now + timedelta(seconds=claim.attempt),
            lease_owner=None,
            lease_until=None,
            updated_at=now,
        )
    )
    return changed.rowcount == 1

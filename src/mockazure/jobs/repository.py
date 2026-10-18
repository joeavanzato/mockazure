from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID

from sqlalchemy import and_, literal_column, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.db.job_model import Job
from mockazure.jobs.schemas import ExportSnapshot, JobClaim, JobView
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
        captured_at=datetime.now(UTC),
        tenant=TenantCreate.model_validate(
            tenant.model_dump(include=set(TenantCreate.model_fields))
        ),
    )
    job = Job(tenant_id=tenant_id, payload=snapshot.model_dump(mode="json"))
    session.add(job)
    await session.flush()
    return JobView.model_validate(job)


async def get_job(session: AsyncSession, tenant_id: UUID, job_id: UUID) -> JobView | None:
    row = await session.scalar(
        select(Job).where(
            Job.id == job_id,
            Job.tenant_id == tenant_id,
            Job.kind == "tenant.export",
        )
    )
    return JobView.model_validate(row) if row else None


async def get_export_result(
    session: AsyncSession,
    tenant_id: UUID,
    job_id: UUID,
) -> ExportSnapshot | None:
    result = await session.scalar(
        select(Job.result).where(
            Job.id == job_id,
            Job.tenant_id == tenant_id,
            Job.state == "succeeded",
            Job.kind == "tenant.export",
        )
    )
    return ExportSnapshot.model_validate(result) if result else None


async def claim_job(
    session: AsyncSession,
    *,
    owner: UUID,
    now: datetime,
    lease_seconds: int,
    tenant_id: UUID | None = None,
    kind: str | None = None,
) -> JobClaim | None:
    if not 1 <= lease_seconds <= 3600:
        raise ValueError("invalid job lease")
    # ARM exhaustion must capture failure evidence under the operation lock order.
    from mockazure.providers.arm.failure import fail_arm_delete

    if kind in {None, "arm.group.delete"}:
        expired_arm = await session.scalar(
            select(Job.id)
            .where(
                True if tenant_id is None else Job.tenant_id == tenant_id,
                Job.kind == "arm.group.delete",
                Job.state == "running",
                Job.attempts >= 3,
                Job.lease_until <= now,
            )
            .order_by(Job.id)
            .limit(1)
        )
        if expired_arm:
            await fail_arm_delete(session, expired_arm, now)
    await session.execute(
        update(Job)
        .where(
            True if tenant_id is None else Job.tenant_id == tenant_id,
            True if kind is None else Job.kind == kind,
            Job.state == "running",
            Job.kind != "arm.group.delete",
            Job.attempts >= 3,
            Job.lease_until <= now,
        )
        .values(
            state="failed",
            error_code="WorkerLeaseExpired",
            lease_owner=None,
            lease_until=None,
            claim_epoch=Job.claim_epoch + 1,
            updated_at=now,
        )
    )
    from mockazure.evidence.risk import unpublished_risk_not_due
    from mockazure.evidence.sources import evidence_job_retired

    row = await session.scalar(
        select(Job)
        .where(
            ~unpublished_risk_not_due(now),
            ~evidence_job_retired(),
            True if tenant_id is None else Job.tenant_id == tenant_id,
            True if kind is None else Job.kind == kind,
            # Static predicates let generic prepared plans use the active-queue index.
            Job.state.in_((literal_column("'pending'"), literal_column("'running'"))),
            Job.attempts < literal_column("3"),
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
    row.claim_epoch += 1
    row.lease_until = now + timedelta(seconds=lease_seconds)
    row.updated_at, row.error_code = now, None
    await session.flush()
    return JobClaim(
        row.id,
        row.tenant_id,
        row.kind,
        owner,
        row.attempts,
        row.claim_epoch,
        row.lease_until,
        row.payload,
    )


def fenced(claim: JobClaim, now: datetime):
    return (
        Job.id == claim.id,
        Job.tenant_id == claim.tenant_id,
        Job.kind == claim.kind,
        Job.state == "running",
        Job.lease_owner == claim.owner,
        Job.attempts == claim.attempt,
        Job.claim_epoch == claim.epoch,
        Job.lease_until == claim.lease_until,
        Job.lease_until > now,
    )


async def lock_claim(session: AsyncSession, claim: JobClaim, now: datetime) -> Job | None:
    started = monotonic()
    row = await session.scalar(
        select(Job)
        .where(*fenced(claim, now))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    checked_at = now + timedelta(seconds=monotonic() - started)
    if row is None or row.lease_until <= checked_at:
        return None
    return row


async def finish_export(
    session: AsyncSession,
    claim: JobClaim,
    result: ExportSnapshot | dict,
    *,
    now: datetime | None = None,
) -> bool:
    if claim.kind != "tenant.export":
        return False
    result = ExportSnapshot.model_validate(result)
    now = now or datetime.now(UTC)
    row = await lock_claim(session, claim, now)
    if row is None:
        return False
    row.state, row.result, row.error_code = "succeeded", result.model_dump(mode="json"), None
    row.lease_owner = row.lease_until = None
    row.updated_at = now
    await session.flush()
    return True


async def fail_job(
    session: AsyncSession,
    claim: JobClaim,
    *,
    error_code: str,
    now: datetime,
    terminal: bool = False,
) -> bool:
    allowed = {
        "TransientError",
        "ExportFailed",
        "InvalidEvidenceSource",
        "InvalidActivitySource",
        "UnsupportedJobKind",
        "ArmOperationFailed",
    }
    code = error_code if error_code in allowed else "ExportFailed"
    if claim.kind == "arm.group.delete":
        from mockazure.providers.arm.failure import fail_arm_delete

        return await fail_arm_delete(
            session, claim.id, now, claim=claim, terminal=terminal, error_code=code
        )
    row = await lock_claim(session, claim, now)
    if row is None:
        return False
    row.state = "failed" if terminal or claim.attempt >= 3 else "pending"
    row.error_code = code
    row.available_at = now + timedelta(seconds=claim.attempt)
    row.lease_owner = row.lease_until = None
    row.updated_at = now
    await session.flush()
    return True

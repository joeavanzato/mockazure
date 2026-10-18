"""Generation reset and bounded cleanup preserve immutable replay barriers."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, delete, exists, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert

from mockazure.admin.errors import AdminError
from mockazure.db.job_model import Job
from mockazure.directory.models import DirectoryState
from mockazure.evidence.clock import evidence_now
from mockazure.evidence.models import (
    EvidenceCursor,
    EvidenceObservation,
    EvidenceSource,
    EvidenceVersion,
    ProjectionReceipt,
)
from mockazure.evidence.repository import lock_state
from mockazure.evidence.schemas import EvidenceSettings
from mockazure.oauth.device_models import AuthenticationOutcome


async def reset_evidence(session, tenant_id, expected_generation):
    from mockazure.security.repository import lock_security

    await lock_security(session, tenant_id)
    # Directory writes take this lock before EvidenceState; preserve that order.
    directory = await session.scalar(
        select(DirectoryState).where(DirectoryState.tenant_id == tenant_id).with_for_update()
    )
    state = await lock_state(session, tenant_id)
    if state.generation != expected_generation:
        raise AdminError(409, "EvidenceGenerationConflict", "Evidence generation has changed")
    latest = await session.scalar(
        select(func.max(AuthenticationOutcome.timestamp)).where(
            AuthenticationOutcome.tenant_id == tenant_id
        )
    )
    state.legacy_auth_cutoff_at = max(datetime.now(UTC), latest) if latest else datetime.now(UTC)
    state.legacy_directory_revision = directory.revision if directory else 0
    from mockazure.cloud.lifecycle import invalidate_cloud

    await invalidate_cloud(session, tenant_id, include_groups=False)
    state.generation = uuid4()
    await session.flush()
    return state.generation


async def cleanup_evidence(session, tenant_id, wall_now, limit=500):
    if not 1 <= limit <= 1000:
        raise ValueError("Cleanup limit must be 1..1000")
    state = await lock_state(session, tenant_id)
    when = await evidence_now(session, tenant_id, wall_now)
    cutoff = when - timedelta(days=EvidenceSettings.model_validate(state.settings).retention_days)
    expired = list(
        await session.scalars(
            select(EvidenceCursor.token_hash)
            .where(EvidenceCursor.tenant_id == tenant_id, EvidenceCursor.expires_at <= wall_now)
            .limit(limit)
        )
    )
    if expired:
        await session.execute(delete(EvidenceCursor).where(EvidenceCursor.token_hash.in_(expired)))
    remaining = limit - len(expired)
    pinned = exists(
        select(1).where(
            EvidenceCursor.tenant_id == EvidenceVersion.tenant_id,
            EvidenceCursor.generation == EvidenceVersion.generation,
            EvidenceCursor.expires_at > wall_now,
            EvidenceCursor.revision >= EvidenceVersion.valid_from,
            or_(
                EvidenceVersion.valid_to.is_(None),
                EvidenceCursor.revision < EvidenceVersion.valid_to,
            ),
            or_(
                EvidenceVersion.family == "riskyUser",
                EvidenceVersion.event_time >= EvidenceCursor.retention_cutoff,
            ),
            EvidenceVersion.event_time <= EvidenceCursor.evidence_time,
            EvidenceVersion.available_at <= EvidenceCursor.evidence_time,
        )
    )
    versions = (
        (
            await session.execute(
                select(
                    EvidenceVersion.family, EvidenceVersion.record_id, EvidenceVersion.valid_from
                )
                .where(
                    EvidenceVersion.tenant_id == tenant_id,
                    or_(
                        EvidenceVersion.generation != state.generation,
                        and_(
                            EvidenceVersion.family != "riskyUser",
                            EvidenceVersion.event_time < cutoff,
                        ),
                        EvidenceVersion.valid_to.is_not(None),
                    ),
                    ~pinned,
                )
                .order_by(EvidenceVersion.event_time, EvidenceVersion.record_id)
                .limit(remaining)
            )
        ).all()
        if remaining
        else []
    )
    if versions:
        await session.execute(
            delete(EvidenceVersion).where(
                EvidenceVersion.tenant_id == tenant_id,
                tuple_(
                    EvidenceVersion.family, EvidenceVersion.record_id, EvidenceVersion.valid_from
                ).in_(versions),
            )
        )
    remaining -= len(versions)
    referenced = exists(
        select(1).where(
            EvidenceVersion.tenant_id == EvidenceObservation.tenant_id,
            EvidenceVersion.family == EvidenceObservation.family,
            EvidenceVersion.record_id == EvidenceObservation.record_id,
        )
    )
    observations = (
        (
            await session.execute(
                select(EvidenceObservation.family, EvidenceObservation.record_id)
                .where(EvidenceObservation.tenant_id == tenant_id, ~referenced)
                .order_by(EvidenceObservation.occurred_at, EvidenceObservation.record_id)
                .limit(remaining)
            )
        ).all()
        if remaining
        else []
    )
    if observations:
        await session.execute(
            delete(EvidenceObservation).where(
                EvidenceObservation.tenant_id == tenant_id,
                tuple_(EvidenceObservation.family, EvidenceObservation.record_id).in_(observations),
            )
        )
    remaining -= len(observations)
    retired = (
        list(
            await session.scalars(
                select(EvidenceSource)
                .where(
                    EvidenceSource.tenant_id == tenant_id,
                    EvidenceSource.generation != state.generation,
                    EvidenceSource.acknowledged_at.is_(None),
                )
                .order_by(EvidenceSource.source_revision)
                .limit(remaining)
            )
        )
        if remaining
        else []
    )
    for source in retired:
        source.acknowledged_at = wall_now
        await session.execute(
            insert(ProjectionReceipt)
            .values(
                tenant_id=tenant_id,
                source_kind=source.kind,
                source_id=source.id,
                generation=source.generation,
                revision=state.publication_revision,
                status="suppressed",
                record_ids=[],
            )
            .on_conflict_do_nothing()
        )
        await session.execute(
            update(Job)
            .where(
                Job.tenant_id == tenant_id,
                Job.kind == "evidence.project",
                Job.source_key == source.kind + ":" + str(source.id),
                Job.state.in_(["pending", "running"]),
            )
            .values(
                state="failed",
                error_code="StaleEvidenceGeneration",
                lease_owner=None,
                lease_until=None,
                claim_epoch=Job.claim_epoch + 1,
                updated_at=wall_now,
            )
        )
    await session.flush()
    return {
        "deleted": len(expired) + len(versions) + len(observations),
        "retired": len(retired),
        "limit": limit,
        "receipts_retained": True,
    }

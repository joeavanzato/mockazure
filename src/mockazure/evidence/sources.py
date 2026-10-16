"""Bounded repair of missing enqueue work; no source locks held while claiming jobs."""

from uuid import UUID, uuid4

from sqlalchemy import String, cast, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.db.job_model import Job
from mockazure.evidence.models import EvidenceSource, EvidenceState
from mockazure.evidence.repository import load_source
from mockazure.evidence.schemas import SourceRef


async def enqueue_projection(session: AsyncSession, ref: SourceRef) -> UUID:
    source = await load_source(session, ref)
    if source is None:
        raise ValueError("Missing evidence source")
    source_key = f"{ref.kind}:{ref.id}"
    inserted = await session.scalar(
        insert(Job)
        .values(
            id=uuid4(),
            tenant_id=ref.tenant_id,
            kind="evidence.project",
            payload={"source": ref.model_dump(mode="json"), "generation": str(source.generation)},
            source_key=source_key,
        )
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "kind", "source_key"],
            index_where=Job.source_key.is_not(None),
        )
        .returning(Job.id)
    )
    if inserted:
        return inserted
    return await session.scalar(
        select(Job.id).where(
            Job.tenant_id == ref.tenant_id,
            Job.kind == "evidence.project",
            Job.source_key == source_key,
        )
    )


async def scan_sources(session: AsyncSession, *, limit: int = 100) -> int:
    if not 1 <= limit <= 1000:
        raise ValueError("Scanner limit must be 1..1000")
    from mockazure.evidence.directory_facts import scan_directory_outbox

    captured = await scan_directory_outbox(session, limit)
    if captured == limit:
        return captured
    from mockazure.evidence.authentication import scan_authentication_outcomes

    captured += await scan_authentication_outcomes(session, limit - captured)
    if captured == limit:
        return captured
    queued = exists(
        select(Job.id).where(
            Job.tenant_id == EvidenceSource.tenant_id,
            Job.kind == "evidence.project",
            Job.source_key == EvidenceSource.kind + ":" + cast(EvidenceSource.id, String),
        )
    )
    rows = list(
        await session.scalars(
            select(EvidenceSource)
            .join(
                EvidenceState,
                EvidenceState.tenant_id == EvidenceSource.tenant_id,
            )
            .where(
                EvidenceSource.acknowledged_at.is_(None),
                EvidenceSource.generation == EvidenceState.generation,
                ~queued,
            )
            .order_by(
                EvidenceSource.occurred_at, EvidenceSource.tenant_id, EvidenceSource.source_revision
            )
            .limit(limit - captured)
        )
    )
    for row in rows:
        await enqueue_projection(
            session, SourceRef(tenant_id=row.tenant_id, kind=row.kind, id=row.id)
        )
    return captured + len(rows)

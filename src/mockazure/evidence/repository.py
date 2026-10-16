"""Transaction-owned evidence persistence with tenant commit ordering."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.evidence.models import (
    EvidenceObservation,
    EvidenceSource,
    EvidenceState,
    EvidenceVersion,
    ProjectionReceipt,
)
from mockazure.evidence.schemas import (
    ProjectionBatch,
    PublishResult,
    SourceEnvelope,
    SourceInput,
    SourceRef,
)
from mockazure.providers.graph.schema_reference import validate_payload


async def lock_state(session: AsyncSession, tenant_id: UUID) -> EvidenceState:
    await session.execute(
        insert(EvidenceState)
        .values(
            tenant_id=tenant_id,
            generation=uuid4(),
            source_revision=0,
            publication_revision=0,
            settings={},
        )
        .on_conflict_do_nothing(index_elements=["tenant_id"])
    )
    return await session.scalar(
        select(EvidenceState)
        .where(EvidenceState.tenant_id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def source_hash(value: SourceInput) -> str:
    return hashlib.sha256(
        json.dumps(
            value.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()


def validate_facts(value: SourceInput) -> None:
    if value.ref.kind == "historical":
        facts = value.facts
        if (
            facts.keys() - {"family", "payload", "version"}
            or not {"family", "payload"} <= facts.keys()
        ):
            raise ValueError("Historical source requires a public payload")
        if facts["family"] not in {"signIn", "directoryAudit", "riskDetection", "riskyUser"}:
            raise ValueError("Unsupported evidence family")
        validate_payload(facts.get("version", "v1.0"), facts["family"], facts["payload"])
        if "appliedConditionalAccessPolicies" in facts["payload"]:
            raise ValueError("Conditional Access policy detail is not supported")
        UUID(facts["payload"]["id"])
    elif value.ref.kind == "directory":
        from mockazure.evidence.directory_facts import validate_directory_facts

        validate_directory_facts(value.facts)
    else:
        raise ValueError("Unknown evidence source kind")


async def append_source(session: AsyncSession, value: SourceInput) -> SourceRef:
    value = SourceInput.model_validate(value.model_dump())
    validate_facts(value)
    digest = source_hash(value)
    state = await lock_state(session, value.ref.tenant_id)
    prior = await session.get(
        EvidenceSource,
        (
            value.ref.tenant_id,
            value.ref.kind,
            value.ref.id,
        ),
    )
    if prior:
        if prior.content_hash != digest:
            raise ValueError("Evidence source content conflict")
        return value.ref
    state.source_revision += 1
    session.add(
        EvidenceSource(
            tenant_id=value.ref.tenant_id,
            kind=value.ref.kind,
            id=value.ref.id,
            generation=state.generation,
            source_revision=state.source_revision,
            occurred_at=value.occurred_at,
            available_at=value.available_at,
            facts=value.facts,
            content_hash=digest,
        )
    )
    await session.flush()
    return value.ref


async def load_source(session: AsyncSession, ref: SourceRef) -> SourceEnvelope | None:
    row = await session.get(EvidenceSource, (ref.tenant_id, ref.kind, ref.id))
    if row is None:
        return None
    return SourceEnvelope(
        ref=ref,
        occurred_at=row.occurred_at,
        available_at=row.available_at,
        facts=row.facts,
        source_revision=row.source_revision,
        generation=row.generation,
    )


async def publish_batch(session: AsyncSession, batch: ProjectionBatch) -> PublishResult:
    batch = ProjectionBatch.model_validate(batch.model_dump())
    # Validate even on replay; a receipt cannot authorize foreign or malformed input.
    identities = set()
    for item in batch.items:
        if item.source_ref != batch.source or str(item.id) != item.payload.get("id"):
            raise ValueError("Projection/source identity mismatch")
        if item.available_at < item.occurred_at:
            raise ValueError("Invalid projection availability")
        key = (item.family, item.id)
        if key in identities:
            raise ValueError("Duplicate projection identity")
        identities.add(key)
    if batch.receipt_status == "suppressed" and batch.items:
        raise ValueError("Suppressed sources cannot publish records")
    state = await lock_state(session, batch.source.tenant_id)
    source = await load_source(session, batch.source)
    if source is None:
        raise ValueError("Missing projection source")
    version = source.facts.get("version", "v1.0") if source.ref.kind == "historical" else "beta"
    for item in batch.items:
        validate_payload(version, item.family, item.payload)
    if batch.generation != state.generation or source.generation != state.generation:
        raise ValueError("Stale evidence generation")
    key = (batch.source.tenant_id, batch.source.kind, batch.source.id)
    receipt = await session.get(ProjectionReceipt, key)
    if receipt:
        return PublishResult(
            revision=receipt.revision,
            record_ids=tuple(receipt.record_ids),
            already_applied=True,
        )
    # Precheck all IDs before writing; constraint remains a second defence.
    for item in batch.items:
        if await session.get(EvidenceObservation, (key[0], item.family, item.id)):
            raise ValueError("Evidence record ID conflict")
    state.publication_revision += 1
    revision = state.publication_revision
    for item in batch.items:
        session.add(
            EvidenceObservation(
                tenant_id=key[0],
                family=item.family,
                record_id=item.id,
                source_kind=key[1],
                source_id=key[2],
                occurred_at=item.occurred_at,
                available_at=item.available_at,
                subject_id=item.subject_id,
                payload=item.payload,
            )
        )
    await session.flush()
    for item in batch.items:
        types = item.payload.get("signInEventTypes", [])
        session.add(
            EvidenceVersion(
                tenant_id=key[0],
                family=item.family,
                record_id=item.id,
                valid_from=revision,
                event_time=item.occurred_at,
                available_at=item.available_at,
                subject_id=item.subject_id,
                app_id=item.payload.get("appId"),
                event_type=types[0] if types else None,
                payload=item.payload,
            )
        )
    record_ids = tuple(item.id for item in batch.items)
    session.add(
        ProjectionReceipt(
            tenant_id=key[0],
            source_kind=key[1],
            source_id=key[2],
            generation=state.generation,
            revision=revision,
            status=batch.receipt_status,
            record_ids=[str(x) for x in record_ids],
        )
    )
    row = await session.get(EvidenceSource, key)
    row.acknowledged_at = datetime.now(UTC)
    if batch.source.kind == "directory":
        from mockazure.directory.models import EvidenceOutbox

        await session.execute(
            update(EvidenceOutbox)
            .where(EvidenceOutbox.tenant_id == key[0], EvidenceOutbox.id == key[2])
            .values(projected_at=row.acknowledged_at)
        )
    await session.flush()
    return PublishResult(revision=revision, record_ids=record_ids, already_applied=False)

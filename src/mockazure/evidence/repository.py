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
            facts.keys()
            - {
                "family",
                "payload",
                "version",
                "provenance",
                "entitlement_snapshot",
                "orphaned_subject",
                "subject_id",
                "sign_in_id",
            }
            or not {"family", "payload"} <= facts.keys()
        ):
            raise ValueError("Historical source requires a public payload")
        if facts["family"] not in {
            "signIn",
            "directoryAudit",
            "riskDetection",
            "riskyUser",
            "auditLogRecord",
        }:
            raise ValueError("Unsupported evidence family")
        if facts["family"] == "auditLogRecord":
            from mockazure.mail.audit_schemas import validate_audit_record

            validate_audit_record(
                facts["payload"], tenant=value.ref.tenant_id, occurred_at=value.occurred_at
            )
        else:
            validate_payload(facts.get("version", "v1.0"), facts["family"], facts["payload"])
        UUID(facts["payload"]["id"])
        if "provenance" in facts:
            from mockazure.evidence.datasets import Provenance

            Provenance.model_validate(facts["provenance"])
        if facts.get("entitlement_snapshot") not in {None, "P1", "P2"}:
            raise ValueError("Invalid historical entitlement")
        if "orphaned_subject" in facts and type(facts["orphaned_subject"]) is not bool:
            raise ValueError("Invalid orphan disclosure marker")
        if facts.get("subject_id"):
            subject = (
                facts["payload"].get("userId")
                if facts["family"] != "riskyUser"
                else facts["payload"]["id"]
            )
            if facts["subject_id"] != subject:
                raise ValueError("Historical subject mismatch")
    elif value.ref.kind in {"risk", "risk_action"}:
        from mockazure.evidence.risk import validate_risk_facts

        parsed = validate_risk_facts(value.ref.kind, value.facts)
        if value.ref.kind == "risk" and parsed.input.request_id != value.ref.id:
            raise ValueError("Risk observation request identity mismatch")
    elif value.ref.kind == "authentication":
        from mockazure.evidence.authentication import AuthenticationFact

        fact = AuthenticationFact.model_validate(value.facts)
        if fact.tenant_id != value.ref.tenant_id or fact.attempt_id != value.ref.id:
            raise ValueError("Authentication source identity mismatch")
    elif value.ref.kind == "mail":
        from mockazure.mail.facts import validate_mail_facts

        validate_mail_facts(value.facts)
    elif value.ref.kind == "security":
        from mockazure.security.audit import validate_security_facts

        validate_security_facts(value.facts)
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
        if item.family == "auditLogRecord":
            from mockazure.mail.audit_schemas import validate_audit_record

            validate_audit_record(
                item.payload, tenant=batch.source.tenant_id, occurred_at=item.occurred_at
            )
        else:
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
    # Risk aggregation alone can version an existing riskyUser observation.
    existing_ids = set()
    for item in batch.items:
        if await session.get(EvidenceObservation, (key[0], item.family, item.id)):
            if source.ref.kind != "risk" or item.family != "riskyUser":
                raise ValueError("Evidence record ID conflict")
            existing_ids.add((item.family, item.id))
    state.publication_revision += 1
    revision = state.publication_revision
    for item in batch.items:
        if (item.family, item.id) in existing_ids:
            continue
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
    from mockazure.evidence.risk import current_version, projection_payload, publish_linked_risk

    for item in batch.items:
        payload = await projection_payload(session, source, item)
        if (item.family, item.id) in existing_ids:
            current = await current_version(session, key[0], item.family, item.id)
            if current is not None:
                current.valid_to = revision
                await session.flush()
        types = item.payload.get("signInEventTypes", [])
        session.add(
            EvidenceVersion(
                tenant_id=key[0],
                generation=state.generation,
                source_kind=key[1],
                source_id=key[2],
                family=item.family,
                record_id=item.id,
                valid_from=revision,
                event_time=projection_time(item.family, payload, item.occurred_at),
                available_at=item.available_at,
                subject_id=item.subject_id,
                app_id=item.payload.get("appId"),
                event_type=types[0] if types else None,
                payload=payload,
            )
        )
    from mockazure.security.activity import record_activity

    for item in batch.items:
        await record_activity(session, key[0], item)
    await session.flush()
    await publish_linked_risk(session, source, revision)
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


def projection_time(family, payload, fallback):
    field = {
        "signIn": "createdDateTime",
        "directoryAudit": "activityDateTime",
        "riskDetection": "detectedDateTime",
        "riskyUser": "riskLastUpdatedDateTime",
        "auditLogRecord": "createdDateTime",
    }[family]
    return datetime.fromisoformat(payload[field]) if payload.get(field) else fallback

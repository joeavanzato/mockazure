"""Generation-fenced publication with permanent identity receipts and leased workers."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid5

from sqlalchemy.dialects.postgresql import insert

from mockazure.activity.contracts import content_type, from_graph, validate_record
from mockazure.activity.models import ActivityState, Content
from mockazure.activity.subscriptions import state
from mockazure.db.job_model import Job
from mockazure.evidence.clock import evidence_now
from mockazure.evidence.models import EvidenceObservation
from mockazure.jobs.repository import fail_job, lock_claim
from mockazure.jobs.schemas import JobResult


def digest(record):
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


async def store_records(session, tenant, records, published_at, *, current=None):
    if published_at.tzinfo is None or len(records) > 1000:
        raise ValueError("Bounded publication and aware timestamp required")
    current = current or await state(session, tenant, lock=True)
    result = []
    for record in records:
        validate_record(record, tenant)
        record_id = UUID(record["Id"])
        identifier = uuid5(current.generation, str(record_id)).hex
        previous = await session.get(Content, (tenant, identifier))
        fingerprint = digest(record)
        if previous is not None:
            if previous.digest != fingerprint:
                raise ValueError("Published activity identity is immutable")
            result.append(identifier)
            continue
        current.revision += 1
        session.add(
            Content(
                tenant_id=tenant,
                id=identifier,
                generation=current.generation,
                record_id=record_id,
                content_type=content_type(record),
                revision=current.revision,
                published_at=published_at,
                digest=fingerprint,
                records=[record],
            )
        )
        await session.flush()
        result.append(identifier)
    return result


async def publish_activity(
    session, tenant_id: UUID, record_ids: list[UUID], published_at: datetime
) -> list[str]:
    if len(record_ids) > 1000:
        raise ValueError("Publication batch exceeds 1000 records")
    records = []
    for identifier in record_ids:
        row = await session.get(EvidenceObservation, (tenant_id, "auditLogRecord", identifier))
        if row is None:
            raise ValueError("Canonical audit record not found")
        records.append(from_graph(row.payload, tenant_id))
    return await store_records(session, tenant_id, records, published_at)


async def enqueue_activity(session, tenant, source_kind, source_id, records):
    current = await session.get(ActivityState, tenant)
    if current is None or not records:
        return
    now = datetime.now(UTC)
    # Copy public facts into the outbox so Graph retention cannot erase pending feed input.
    await session.execute(
        insert(Job)
        .values(
            tenant_id=tenant,
            kind="activity.publish",
            source_key=str(uuid5(current.generation, f"{source_kind}:{source_id}")),
            available_at=now + timedelta(seconds=current.settings.get("delay_seconds", 0)),
            payload={"generation": str(current.generation), "records": records},
            state="pending",
            attempts=0,
            claim_epoch=0,
        )
        .on_conflict_do_nothing(
            index_elements=[Job.tenant_id, Job.kind, Job.source_key],
            index_where=Job.source_key.is_not(None),
        )
    )


class PublicationLeaseExpired(Exception):
    pass


async def process_activity(database, claim, wall_now):
    started = monotonic()

    def now():
        return wall_now + timedelta(seconds=monotonic() - started)

    try:
        async with database.sessions.begin() as session:
            published = await evidence_now(session, claim.tenant_id, wall_now)
            current = await state(session, claim.tenant_id, create=False, lock=True)
            job = await lock_claim(session, claim, now())
            if job is None:
                return JobResult("stale", None, None)
            if current is None or claim.payload.get("generation") != str(current.generation):
                job.state, job.error_code = "failed", "StaleActivityGeneration"
                job.lease_owner = job.lease_until = None
                return JobResult("stale", None, "StaleActivityGeneration")
            if claim.attempt <= current.settings.get("fail_attempts", 0):
                await fail_job(session, claim, error_code="TransientError", now=now())
                return JobResult("pending", None, "TransientError")
            if set(claim.payload) != {"generation", "records"} or not isinstance(
                claim.payload["records"], list
            ):
                raise ValueError("Invalid activity job")
            records = [from_graph(value, claim.tenant_id) for value in claim.payload["records"]]
            identifiers = await store_records(
                session, claim.tenant_id, records, published, current=current
            )
            if job.lease_until <= now():
                raise PublicationLeaseExpired()
            job.state, job.result, job.error_code = "succeeded", {"content_ids": identifiers}, None
            job.lease_owner = job.lease_until = None
            job.payload = {"generation": str(current.generation)}
            job.updated_at = now()
            await session.flush()
            return JobResult("succeeded", job.result, None)
    except PublicationLeaseExpired:
        return JobResult("stale", None, None)

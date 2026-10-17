"""Bounded historical sign-in publication using the same receipts and tenant fence.

Claims and publications share a transaction: a killed worker rolls both back.
Only pending jobs enter this path; running/reclaimed and other families continue
through the ordinary dispatcher. Each source still gets its own revision/receipt.
"""

from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID

from sqlalchemy import insert, select

from mockazure.db.job_model import Job
from mockazure.evidence.models import (
    EvidenceObservation,
    EvidenceSource,
    EvidenceVersion,
    ProjectionReceipt,
    RiskActionCutoff,
)
from mockazure.evidence.projection import LeaseExpired
from mockazure.evidence.projectors import project_source
from mockazure.evidence.repository import lock_state, projection_time
from mockazure.evidence.risk import apply_risk_cutoff
from mockazure.evidence.schemas import SourceEnvelope, SourceRef
from mockazure.identity.models import User
from mockazure.security.activity import UserSignInActivity


def pending(now, tenant_id=None):
    from mockazure.evidence.sources import evidence_job_retired

    return (
        select(Job)
        .where(
            Job.kind == "evidence.project",
            Job.state == "pending",
            Job.attempts < 3,
            Job.available_at <= now,
            Job.source_key.like("historical:%"),
            ~evidence_job_retired(),
            True if tenant_id is None else Job.tenant_id == tenant_id,
        )
        .order_by(Job.available_at, Job.id)
    )


async def write_batch(session, state, pairs, owner, now):
    sources = [source for _, source in pairs]
    receipts = {
        row.source_id: row
        for row in await session.scalars(
            select(ProjectionReceipt).where(
                ProjectionReceipt.tenant_id == state.tenant_id,
                ProjectionReceipt.source_kind == "historical",
                ProjectionReceipt.source_id.in_([source.id for source in sources]),
            )
        )
    }
    projected = []
    for job, source in pairs:
        if set(job.payload) != {"source", "generation"}:
            raise ValueError("Invalid historical projection job")
        ref = SourceRef(tenant_id=state.tenant_id, kind="historical", id=source.id)
        if (
            SourceRef.model_validate(job.payload["source"]) != ref
            or UUID(job.payload["generation"]) != state.generation
        ):
            raise ValueError("Historical projection job identity mismatch")
        envelope = SourceEnvelope(
            ref=ref,
            generation=source.generation,
            source_revision=source.source_revision,
            occurred_at=source.occurred_at,
            available_at=source.available_at,
            facts=source.facts,
        )
        batch = project_source(envelope)  # Same pinned schema and fact validation on replay.
        if len(batch.items) != 1 or batch.items[0].family != "signIn":
            raise ValueError("Unsupported bulk projection")
        item = batch.items[0]
        projected.append((job, source, item))
        job.state, job.lease_owner = "running", owner
        job.lease_until = now + timedelta(seconds=30)
        job.attempts += 1
        job.claim_epoch += 1

    fresh = [(job, source, item) for job, source, item in projected if source.id not in receipts]
    ids = [item.id for _, _, item in fresh]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate historical record identity")
    if ids and await session.scalar(
        select(EvidenceObservation.record_id)
        .where(
            EvidenceObservation.tenant_id == state.tenant_id,
            EvidenceObservation.family == "signIn",
            EvidenceObservation.record_id.in_(ids),
        )
        .limit(1)
    ):
        raise ValueError("Historical record ID conflict")
    subjects = {item.subject_id for _, _, item in fresh if item.subject_id is not None}
    cutoffs = (
        {
            row.user_id: row
            for row in await session.scalars(
                select(RiskActionCutoff).where(
                    RiskActionCutoff.tenant_id == state.tenant_id,
                    RiskActionCutoff.generation == state.generation,
                    RiskActionCutoff.user_id.in_(subjects),
                )
            )
        }
        if subjects
        else {}
    )
    live_users = (
        set(
            await session.scalars(
                select(User.id).where(
                    User.tenant_id == state.tenant_id,
                    User.id.in_(subjects),
                    User.deleted_at.is_(None),
                )
            )
        )
        if subjects
        else set()
    )
    observations, versions, activities, receipt_rows = [], [], [], []
    for job, source, item in projected:
        prior = receipts.get(source.id)
        if prior is not None:
            job.result = {
                "revision": prior.revision,
                "record_ids": prior.record_ids,
                "already_applied": True,
            }
        else:
            state.publication_revision += 1
            revision = state.publication_revision
            identity = dict(
                tenant_id=state.tenant_id,
                family="signIn",
                record_id=item.id,
                source_kind="historical",
                source_id=source.id,
            )
            observations.append(
                {
                    **identity,
                    "occurred_at": item.occurred_at,
                    "available_at": item.available_at,
                    "subject_id": item.subject_id,
                    "payload": item.payload,
                }
            )
            payload = apply_risk_cutoff(
                item.payload,
                cutoffs.get(item.subject_id),
                occurred_at=item.occurred_at,
                source_revision=source.source_revision,
                historical=True,
            )
            types = item.payload.get("signInEventTypes", [])
            versions.append(
                {
                    **identity,
                    "generation": state.generation,
                    "valid_from": revision,
                    "event_time": projection_time("signIn", payload, item.occurred_at),
                    "available_at": item.available_at,
                    "subject_id": item.subject_id,
                    "app_id": item.payload.get("appId"),
                    "event_type": types[0] if types else None,
                    "payload": payload,
                }
            )
            interactive = item.payload.get("isInteractive")
            code = (item.payload.get("status") or {}).get("errorCode")
            if item.subject_id in live_users and type(interactive) is bool and type(code) is int:
                activities.append(
                    {
                        "tenant_id": state.tenant_id,
                        "record_id": item.id,
                        "user_id": item.subject_id,
                        "occurred_at": item.occurred_at,
                        "available_at": item.available_at,
                        "interactive": interactive,
                        "successful": code == 0,
                    }
                )
            receipt_rows.append(
                {
                    "tenant_id": state.tenant_id,
                    "source_kind": "historical",
                    "source_id": source.id,
                    "generation": state.generation,
                    "revision": revision,
                    "status": "projected",
                    "record_ids": [str(item.id)],
                }
            )
            source.acknowledged_at = datetime.now(UTC)
            job.result = {
                "revision": revision,
                "record_ids": [str(item.id)],
                "already_applied": False,
            }
        job.state, job.error_code = "succeeded", None
        job.lease_owner = job.lease_until = None
        job.updated_at = now
    for model, rows in (
        (EvidenceObservation, observations),
        (EvidenceVersion, versions),
        (UserSignInActivity, activities),
        (ProjectionReceipt, receipt_rows),
    ):
        if rows:
            await session.execute(insert(model), rows)
    await session.flush()


async def run_historical_batch(database, *, owner, now, limit=100):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Historical batch limit must be 1..100")
    started = monotonic()
    try:
        async with database.sessions.begin() as session:
            candidate = await session.scalar(pending(now).limit(1))
            if candidate is None:
                return 0
            # Bound job selection BEFORE loading source facts. A join before LIMIT
            # made PostgreSQL inspect the full backlog on every publication.
            state = await lock_state(session, candidate.tenant_id)
            current = now + timedelta(seconds=monotonic() - started)
            jobs = list(
                await session.scalars(
                    pending(current, state.tenant_id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
            )
            identifiers = {}
            for job in jobs:
                try:
                    identifiers[job.id] = UUID(job.source_key.removeprefix("historical:"))
                except (ValueError, AttributeError):
                    continue  # The ordinary dispatcher classifies malformed work.
            sources = (
                {
                    source.id: source
                    for source in await session.scalars(
                        select(EvidenceSource).where(
                            EvidenceSource.tenant_id == state.tenant_id,
                            EvidenceSource.kind == "historical",
                            EvidenceSource.id.in_(identifiers.values()),
                            EvidenceSource.generation == state.generation,
                            EvidenceSource.facts["family"].astext == "signIn",
                        )
                    )
                }
                if identifiers
                else {}
            )
            pairs = [
                (job, sources[identifiers[job.id]])
                for job in jobs
                if job.id in identifiers and identifiers[job.id] in sources
            ]
            if not pairs:
                return 0
            await write_batch(session, state, pairs, owner, current)
            if now + timedelta(seconds=monotonic() - started) >= current + timedelta(seconds=30):
                raise LeaseExpired()
            return len(pairs)
    except LeaseExpired:
        return 0

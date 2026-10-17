"""Historical batching keeps the ordinary projection's fences and atomic output."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from evidence_support import tenant
from sqlalchemy import func, select, update

from mockazure.db.job_model import Job
from mockazure.evidence.datasets import EvidenceDataset, import_evidence
from mockazure.evidence.models import (
    EvidenceObservation,
    EvidenceSource,
    EvidenceState,
    EvidenceVersion,
    ProjectionReceipt,
)
from mockazure.evidence.projection import process_projection
from mockazure.jobs.repository import claim_job
from tools.scale_dataset import iter_evidence_batches


async def seed(database, count=7):
    async with database.sessions.begin() as session:
        owner = await tenant(session)
        dataset = EvidenceDataset.model_validate(next(iter_evidence_batches(99, 2, count)))
        await import_evidence(session, owner, dataset)
    return owner


async def counts(database, owner):
    async with database.sessions() as session:
        return [
            await session.scalar(
                select(func.count()).select_from(model).where(model.tenant_id == owner)
            )
            for model in (EvidenceObservation, EvidenceVersion, ProjectionReceipt)
        ]


async def test_bounded_batch_two_workers_receipts_and_replay(database):
    from mockazure.evidence.bulk import run_historical_batch

    owner = await seed(database)
    now = datetime.now(UTC) + timedelta(seconds=1)
    results = await asyncio.gather(
        *(run_historical_batch(database, owner=uuid4(), now=now, limit=3) for _ in range(2))
    )
    assert sum(results) == 6
    assert await counts(database, owner) == [6, 6, 6]
    assert await run_historical_batch(database, owner=uuid4(), now=now, limit=3) == 1
    assert await run_historical_batch(database, owner=uuid4(), now=now, limit=3) == 0
    async with database.sessions.begin() as session:
        jobs = list(await session.scalars(select(Job).where(Job.tenant_id == owner)))
        assert all(j.state == "succeeded" and j.claim_epoch == 1 and j.attempts == 1 for j in jobs)
        assert all(j.lease_owner is None and j.lease_until is None for j in jobs)
        revisions = list(
            await session.scalars(
                select(ProjectionReceipt.revision).where(ProjectionReceipt.tenant_id == owner)
            )
        )
        assert sorted(revisions) == list(range(1, 8))
        jobs[0].state = "pending"
    assert await run_historical_batch(database, owner=uuid4(), now=now, limit=3) == 1
    assert await counts(database, owner) == [7, 7, 7]


async def test_batch_fences_old_claim_and_old_generation(database):
    from mockazure.evidence.bulk import run_historical_batch

    owner = await seed(database, 2)
    now = datetime.now(UTC) + timedelta(seconds=1)
    async with database.sessions.begin() as session:
        stale = await claim_job(session, owner=uuid4(), now=now, lease_seconds=30)
        row = await session.get(Job, stale.id)
        row.state, row.attempts = "pending", 0
        row.claim_epoch += 1
        row.lease_owner = row.lease_until = None
    assert await run_historical_batch(database, owner=uuid4(), now=now) == 2
    assert (await process_projection(database, stale, now)).state == "stale"
    assert await counts(database, owner) == [2, 2, 2]
    async with database.sessions.begin() as session:
        await session.execute(update(Job).where(Job.tenant_id == owner).values(state="pending"))
        state = await session.get(EvidenceState, owner)
        state.generation = uuid4()
    assert await run_historical_batch(database, owner=uuid4(), now=now) == 0


async def test_batch_failure_after_writes_rolls_back_everything(database, monkeypatch):
    from mockazure.evidence import bulk

    owner = await seed(database)
    original = bulk.write_batch

    async def interrupted(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("interrupt after insertion")

    monkeypatch.setattr(bulk, "write_batch", interrupted)
    with pytest.raises(RuntimeError):
        await bulk.run_historical_batch(database, owner=uuid4(), now=datetime.now(UTC))
    assert await counts(database, owner) == [0, 0, 0]
    async with database.sessions() as session:
        jobs = list(await session.scalars(select(Job).where(Job.tenant_id == owner)))
        assert all(j.state == "pending" and j.claim_epoch == 0 for j in jobs)
        sources = list(await session.scalars(select(EvidenceSource)))
        assert all(s.acknowledged_at is None for s in sources)


async def test_batch_expiry_rolls_back_instead_of_partial_completion(database, monkeypatch):
    from mockazure.evidence import bulk

    owner = await seed(database)
    original = bulk.write_batch

    async def expired(*args, **kwargs):
        await original(*args, **kwargs)
        monkeypatch.setattr(bulk, "monotonic", lambda: initial + 31)

    initial = bulk.monotonic()
    monkeypatch.setattr(bulk, "monotonic", lambda: initial)
    monkeypatch.setattr(bulk, "write_batch", expired)
    assert await bulk.run_historical_batch(database, owner=uuid4(), now=datetime.now(UTC)) == 0
    assert await counts(database, owner) == [0, 0, 0]


async def test_batch_does_not_steal_active_lease(database):
    from mockazure.evidence.bulk import run_historical_batch

    owner = await seed(database, 2)
    now = datetime.now(UTC) + timedelta(seconds=1)
    async with database.sessions.begin() as session:
        claim = await claim_job(session, owner=uuid4(), now=now, lease_seconds=30)
    assert await run_historical_batch(database, owner=uuid4(), now=now) == 1
    assert (await process_projection(database, claim, now)).state == "succeeded"
    assert await counts(database, owner) == [2, 2, 2]


async def test_scale_source_lookup_and_claim_order_indexes(database):
    from sqlalchemy import text

    async with database.sessions() as session:
        indexes = dict(
            (
                await session.execute(
                    text("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public'")
                )
            ).all()
        )
    assert "ix_evidence_source_key" in indexes
    assert "ix_jobs_ready_order" in indexes
    assert "id" in indexes["ix_jobs_claim"].split("available_at", 1)[1]


async def test_batch_matches_regular_cutoff_and_signin_activity(database):
    from uuid import UUID

    from mockazure.directory.datasets import DirectoryDataset, import_directory
    from mockazure.evidence.bulk import run_historical_batch
    from mockazure.evidence.models import RiskActionCutoff
    from mockazure.security.activity import UserSignInActivity
    from tools.scale_dataset import BASE, iter_directory_batches, user_id

    async with database.sessions.begin() as session:
        owner = await tenant(session, slug="scale-99")
        await import_directory(
            session,
            owner,
            DirectoryDataset.model_validate(next(iter_directory_batches(99, 2))),
            "create",
        )
        raw = next(iter_evidence_batches(99, 2, 2))
        for item in raw["records"]:
            item["payload"].update(
                riskState="atRisk", riskLevelAggregated="medium", riskDetail="none"
            )
        await import_evidence(session, owner, EvidenceDataset.model_validate(raw))
        state = await session.get(EvidenceState, owner)
        for index in range(2):
            when = BASE + timedelta(days=1)
            session.add(
                RiskActionCutoff(
                    tenant_id=owner,
                    user_id=UUID(user_id(99, index)),
                    generation=state.generation,
                    occurred_at=when,
                    source_revision=100,
                    action="dismiss",
                    payload={"dismiss": {"occurred_at": when.isoformat(), "source_revision": 100}},
                )
            )
    now = datetime.now(UTC) + timedelta(seconds=1)
    async with database.sessions.begin() as session:
        claim = await claim_job(
            session, owner=uuid4(), now=now, lease_seconds=30, kind="evidence.project"
        )
    assert (await process_projection(database, claim, now)).state == "succeeded"
    assert await run_historical_batch(database, owner=uuid4(), now=now) == 1
    async with database.sessions() as session:
        values = list(await session.scalars(select(EvidenceVersion)))
        assert len(values) == 2
        assert all(row.payload["riskState"] == "dismissed" for row in values)
        assert all(row.payload["riskLevelAggregated"] == "none" for row in values)
        activities = list(await session.scalars(select(UserSignInActivity)))
        assert len(activities) == 2 and all(row.successful for row in activities)
        originals = list(await session.scalars(select(EvidenceObservation)))
        assert all(row.payload["riskState"] == "atRisk" for row in originals)


async def test_retired_generation_cannot_starve_new_pending_work(database):
    from mockazure.evidence.bulk import run_historical_batch

    owner = await seed(database, 2)
    async with database.sessions.begin() as session:
        state = await session.get(EvidenceState, owner)
        state.generation = uuid4()
        await session.flush()
        await import_evidence(
            session, owner, EvidenceDataset.model_validate(next(iter_evidence_batches(100, 2, 2)))
        )
    assert (
        await run_historical_batch(
            database, owner=uuid4(), now=datetime.now(UTC) + timedelta(seconds=1), limit=2
        )
        == 2
    )
    assert await counts(database, owner) == [2, 2, 2]


async def test_scheduled_repair_keeps_unqueued_source_recovery(database):
    from evidence_support import source
    from mockazure.evidence.repository import append_source
    from mockazure.jobs.worker import run_one

    async with database.sessions.begin() as session:
        owner = await tenant(session)
        await append_source(session, source(owner))
    now = datetime.now(UTC) + timedelta(seconds=1)
    assert not await run_one(database, owner=uuid4(), now=now, repair=False)
    assert await counts(database, owner) == [0, 0, 0]
    assert await run_one(database, owner=uuid4(), now=now, repair=True)
    assert await counts(database, owner) == [1, 1, 1]

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from evidence_support import source, tenant, visible_record_ids
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from mockazure.admin.app import create_app
from mockazure.bootstrap import initialize_instance
from mockazure.db.job_model import Job
from mockazure.evidence.models import EvidenceSource, EvidenceState, ProjectionReceipt
from mockazure.evidence.repository import append_source
from mockazure.jobs.repository import claim_job, enqueue_export, get_export_result
from mockazure.jobs.worker import run_one


async def test_mixed_jobs_keep_export_contract(database, settings):
    from mockazure.evidence.sources import enqueue_projection

    async with database.sessions.begin() as session:
        owner = await tenant(session)
        exported = await enqueue_export(session, owner)
        ref = await append_source(session, source(owner))
        projected = await enqueue_projection(session, ref)
        assert await enqueue_projection(session, ref) == projected
    now = datetime.now(UTC) + timedelta(seconds=1)
    assert await run_one(database, owner=uuid4(), now=now)
    assert await run_one(database, owner=uuid4(), now=now)
    assert not await run_one(database, owner=uuid4(), now=now)
    async with database.sessions() as session:
        snapshot = await get_export_result(session, owner, exported.id)
        assert snapshot.source_tenant_id == owner
        assert await get_export_result(session, owner, projected) is None
        assert len(await visible_record_ids(session, owner)) == 1
        assert (await session.get(Job, projected)).state == "succeeded"
    settings.token_file.write_text("A" * 43)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings, database)),
        base_url="https://localhost:8443",
        headers={"Authorization": "Bearer " + "A" * 43},
    ) as client:
        response = await client.get(f"/_mock/tenants/{owner}/jobs/{projected}/result")
        assert response.status_code == 404
        response = await client.get(f"/_mock/tenants/{owner}/jobs/{exported.id}/result")
        assert response.status_code == 200 and response.json()["schema_version"] == 1


async def test_worker_takeover_and_atomic_receipt(database, settings, tmp_path):
    from mockazure.evidence.sources import enqueue_projection

    files = initialize_instance(
        tmp_path / "instance",
        database_url=settings.database_url.get_secret_value(),
        advertised_host="localhost",
        port=8443,
    )
    async with database.sessions.begin() as session:
        owner = await tenant(session)
        ref = await append_source(session, source(owner))
        job_id = await enqueue_projection(session, ref)
    marker = tmp_path / "claimed.txt"
    environment = {k: v for k, v in os.environ.items() if not k.startswith("MOCKAZURE_")}
    with (tmp_path / "child.log").open("w") as stream:
        child = subprocess.Popen(
            [
                sys.executable,
                "tests/support/claim_and_wait.py",
                str(files.config_file),
                str(marker),
            ],
            stdout=stream,
            stderr=stream,
            env=environment,
        )
        try:
            for _ in range(150):
                if marker.exists():
                    break
                assert child.poll() is None
                await asyncio.sleep(0.1)
            assert marker.read_text() == str(job_id)
            child.kill()
            child.wait(timeout=10)
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
    now = datetime.now(UTC) + timedelta(seconds=2)
    await asyncio.gather(*(run_one(database, owner=uuid4(), now=now) for _ in range(2)))
    async with database.sessions() as session:
        assert len(await visible_record_ids(session, owner)) == 1
        assert await session.scalar(select(func.count()).select_from(ProjectionReceipt)) == 1
        row = await session.get(Job, job_id)
        assert row.state == "succeeded" and row.attempts == 2
        original = await session.get(EvidenceSource, (owner, ref.kind, ref.id))
        assert original.acknowledged_at is not None


async def test_retry_epoch_and_reset_fence(database):
    from mockazure.evidence.projection import process_projection
    from mockazure.evidence.sources import enqueue_projection

    async with database.sessions.begin() as session:
        owner = await tenant(session)
        ref = await append_source(session, source(owner))
        job_id = await enqueue_projection(session, ref)
    now = datetime.now(UTC) + timedelta(seconds=1)
    worker = uuid4()
    async with database.sessions.begin() as session:
        stale = await claim_job(session, owner=worker, now=now, lease_seconds=30)
    async with database.sessions.begin() as session:
        row = await session.get(Job, job_id)
        row.state, row.attempts = "pending", 0
        row.claim_epoch += 1
        row.lease_owner = row.lease_until = None
    async with database.sessions.begin() as session:
        current = await claim_job(session, owner=worker, now=now, lease_seconds=30)
    assert stale.attempt == current.attempt == 1
    assert stale.epoch != current.epoch
    assert (await process_projection(database, stale, now)).state == "stale"
    async with database.sessions.begin() as session:
        state = await session.get(EvidenceState, owner)
        state.generation = uuid4()
    assert (await process_projection(database, current, now)).state == "stale"
    async with database.sessions() as session:
        assert await visible_record_ids(session, owner) == []
        assert await session.scalar(select(func.count()).select_from(ProjectionReceipt)) == 0


async def test_failure_classes_and_scanner_repair(database, monkeypatch):
    from mockazure.evidence import projection
    from mockazure.evidence.sources import scan_sources
    from mockazure.jobs.dispatch import dispatch_job

    async with database.sessions.begin() as session:
        owner = await tenant(session)
        await append_source(session, source(owner))
        assert await scan_sources(session) == 1
        assert await scan_sources(session) == 0
    now = datetime.now(UTC) + timedelta(seconds=1)
    original_projector = projection.project_source

    def unavailable(_):
        raise OperationalError("simulated", None, Exception("must-not-leak-diagnostic"))

    monkeypatch.setattr(projection, "project_source", unavailable)
    for attempt in range(1, 4):
        async with database.sessions.begin() as session:
            claim = await claim_job(session, owner=uuid4(), now=now, lease_seconds=30)
            assert claim.attempt == attempt
        result = await dispatch_job(database, claim, now)
        assert result.state == ("failed" if attempt == 3 else "pending")
        async with database.sessions() as session:
            row = await session.get(Job, claim.id)
            assert row.error_code == "TransientError"
            assert (
                now + timedelta(seconds=attempt)
                <= row.available_at
                < now + timedelta(seconds=attempt + 5)
            )
            next_attempt = row.available_at
            assert await visible_record_ids(session, owner) == []
        now = next_attempt
    monkeypatch.setattr(projection, "project_source", original_projector)
    async with database.sessions.begin() as session:
        poison = await append_source(session, source(owner))
        row = await session.get(EvidenceSource, (owner, poison.kind, poison.id))
        row.facts = {"family": "directoryAudit", "payload": {"private_key": "do-not-log"}}
        other = await tenant(session, "other")
        await append_source(session, source(other))
        assert await scan_sources(session) == 2
    for _ in range(2):
        assert await run_one(database, owner=uuid4(), now=now)
    async with database.sessions() as session:
        rows = list(await session.scalars(select(Job).order_by(Job.created_at)))
        assert sorted((r.state, r.attempts) for r in rows) == [
            ("failed", 1),
            ("failed", 3),
            ("succeeded", 1),
        ]
        assert len(await visible_record_ids(session, other)) == 1
        assert "do-not-log" not in str([(r.result, r.error_code) for r in rows])


async def test_expired_claim_cannot_publish_even_before_takeover(database):
    from mockazure.evidence.projection import process_projection
    from mockazure.evidence.sources import enqueue_projection

    async with database.sessions.begin() as session:
        owner = await tenant(session)
        ref = await append_source(session, source(owner))
        await enqueue_projection(session, ref)
    now = datetime.now(UTC) + timedelta(seconds=1)
    async with database.sessions.begin() as session:
        claim = await claim_job(session, owner=uuid4(), now=now, lease_seconds=1)
    result = await process_projection(database, claim, now + timedelta(seconds=2))
    assert result.state == "stale"
    async with database.sessions() as session:
        assert await visible_record_ids(session, owner) == []


@pytest.mark.parametrize("operation", ["export", "failure", "projection"])
async def test_lease_expiring_while_waiting_for_job_lock_is_rejected(database, operation):
    from mockazure.evidence.projection import process_projection
    from mockazure.evidence.sources import enqueue_projection
    from mockazure.jobs.repository import fail_job, finish_export

    async with database.sessions.begin() as session:
        owner = await tenant(session)
        if operation == "projection":
            ref = await append_source(session, source(owner))
            job_id = await enqueue_projection(session, ref)
        else:
            job_id = (await enqueue_export(session, owner)).id
        row = await session.get(Job, job_id)
        row.available_at = datetime.now(UTC) - timedelta(seconds=1)
    now = datetime.now(UTC)
    async with database.sessions.begin() as session:
        claim = await claim_job(session, owner=uuid4(), now=now, lease_seconds=1)
    started = asyncio.Event()

    async def complete():
        started.set()
        if operation == "projection":
            result = await process_projection(database, claim, now)
            return result.state == "succeeded"
        async with database.sessions.begin() as session:
            if operation == "export":
                return await finish_export(session, claim, claim.payload, now=now)
            return await fail_job(session, claim, error_code="TransientError", now=now)

    async with database.sessions() as blocker:
        await blocker.scalar(select(Job).where(Job.id == job_id).with_for_update())
        task = asyncio.create_task(complete())
        try:
            await started.wait()
            done, _ = await asyncio.wait({task}, timeout=1.2)
            assert not done
            await blocker.commit()
            assert await asyncio.wait_for(task, 5) is False
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    async with database.sessions() as session:
        assert (await session.get(Job, job_id)).state == "running"
        assert await visible_record_ids(session, owner) == []


async def test_retry_delay_starts_after_a_slow_failure(database, monkeypatch):
    from mockazure.evidence.sources import enqueue_projection
    from mockazure.jobs import dispatch

    async with database.sessions.begin() as session:
        owner = await tenant(session)
        ref = await append_source(session, source(owner))
        job_id = await enqueue_projection(session, ref)
        row = await session.get(Job, job_id)
        row.available_at = datetime.now(UTC) - timedelta(seconds=1)
    now = datetime.now(UTC)
    async with database.sessions.begin() as session:
        claim = await claim_job(session, owner=uuid4(), now=now, lease_seconds=30)

    async def slow_failure(database, claim, now):
        await asyncio.sleep(1.1)
        raise OperationalError("unavailable", None, Exception("private diagnostic"))

    monkeypatch.setitem(dispatch.HANDLERS, "evidence.project", slow_failure)
    result = await dispatch.dispatch_job(database, claim, datetime.now(UTC))
    assert result.state == "pending"
    async with database.sessions.begin() as session:
        assert (
            await claim_job(session, owner=uuid4(), now=datetime.now(UTC), lease_seconds=30) is None
        )

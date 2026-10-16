from datetime import datetime, timedelta, timezone
from uuid import uuid4

from mockazure.jobs.repository import claim_job, enqueue_export, fail_job, finish_export, get_job
from mockazure.jobs.worker import run_one
from mockazure.tenants.repository import create_tenant
from mockazure.tenants.schemas import TenantCreate


async def seed(database, tenant_payload, count=1):
    async with database.sessions.begin() as session:
        tenant = await create_tenant(session, TenantCreate(**tenant_payload))
        jobs = [await enqueue_export(session, tenant.id) for _ in range(count)]
    return tenant, jobs


async def test_skip_locked_claims_are_distinct(database, tenant_payload):
    await seed(database, tenant_payload, 2)
    now = datetime.now(timezone.utc)
    async with database.sessions.begin() as first_session:
        first = await claim_job(first_session, owner=uuid4(), now=now, lease_seconds=30)
        async with database.sessions.begin() as second_session:
            second = await claim_job(second_session, owner=uuid4(), now=now, lease_seconds=30)
            assert first.id != second.id


async def test_expired_owner_cannot_complete_or_fail_reclaimed_job(database, tenant_payload):
    tenant, jobs = await seed(database, tenant_payload)
    now = datetime.now(timezone.utc)
    owner = uuid4()
    async with database.sessions.begin() as session:
        stale = await claim_job(session, owner=owner, now=now, lease_seconds=1)
    async with database.sessions.begin() as session:
        current = await claim_job(
            session, owner=owner, now=now + timedelta(seconds=2), lease_seconds=30
        )
        assert current.attempt == 2
    async with database.sessions.begin() as session:
        assert not await finish_export(session, stale, stale.payload)
        assert not await fail_job(session, stale, error_code="TransientError", now=now)
        assert await finish_export(session, current, current.payload)
        assert not await finish_export(session, current, current.payload)
    async with database.sessions() as session:
        assert (await get_job(session, tenant.id, jobs[0].id)).state == "succeeded"


async def test_retry_delays_and_terminal_failure(database, tenant_payload):
    tenant, jobs = await seed(database, tenant_payload)
    now = datetime.now(timezone.utc)
    for attempt in range(1, 4):
        async with database.sessions.begin() as session:
            claim = await claim_job(session, owner=uuid4(), now=now, lease_seconds=30)
            assert claim.attempt == attempt
            assert await fail_job(session, claim, error_code="TransientError", now=now)
        async with database.sessions.begin() as session:
            assert await claim_job(session, owner=uuid4(), now=now, lease_seconds=30) is None
        now += timedelta(seconds=attempt)
    async with database.sessions() as session:
        job = await get_job(session, tenant.id, jobs[0].id)
        assert job.state == "failed" and job.attempts == 3


async def test_expired_final_claim_is_reaped(database, tenant_payload):
    tenant, jobs = await seed(database, tenant_payload)
    now = datetime.now(timezone.utc)
    for _ in range(3):
        async with database.sessions.begin() as session:
            assert await claim_job(session, owner=uuid4(), now=now, lease_seconds=1)
        now += timedelta(seconds=2)
    async with database.sessions.begin() as session:
        assert await claim_job(session, owner=uuid4(), now=now, lease_seconds=1) is None
        assert (await get_job(session, tenant.id, jobs[0].id)).state == "failed"


async def test_worker_finishes_once(database, tenant_payload):
    tenant, jobs = await seed(database, tenant_payload)
    assert await run_one(database, owner=uuid4(), now=datetime.now(timezone.utc))
    assert not await run_one(database, owner=uuid4(), now=datetime.now(timezone.utc))
    async with database.sessions() as session:
        job = await get_job(session, tenant.id, jobs[0].id)
        assert job.state == "succeeded" and job.attempts == 1

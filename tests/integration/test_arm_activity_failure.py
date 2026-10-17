from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from mockazure.db.job_model import Job
from mockazure.evidence.models import EvidenceSource, EvidenceVersion
from mockazure.identity.models import ServicePrincipal
from mockazure.jobs.dispatch import HANDLERS, dispatch_job
from mockazure.jobs.repository import claim_job
from mockazure.providers.arm.models import ArmResource
from tests.integration.graph_support import graph_client
from tests.integration.test_arm_authorization import assign
from tests.integration.test_arm_inventory import access
from tests.integration.test_arm_state import seed_arm


@pytest.mark.parametrize("failure", ["terminal", "exhausted"])
async def test_arm_failed_delete_atomically_captures_failure(database, settings, failure):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, scope, resource = await seed_arm(client, tenant)
        evidence = f"/_mock/tenants/{tenant}/evidence"
        assert (
            await client.patch(
                evidence + "/settings",
                json={
                    "delay_seconds": 0,
                    "clock_mode": "fixed",
                    "fixed_time": "2026-10-02T00:00:00Z",
                },
            )
        ).status_code == 200
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(client, base, sp, f"/subscriptions/{sub}", "Owner")
        started = await client.delete(scope, params={"api-version": "2021-04-01"}, headers=headers)
        assert started.status_code == 202
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            claim = await claim_job(
                session,
                owner=uuid4(),
                now=now,
                lease_seconds=30 if failure == "terminal" else 1,
                tenant_id=tenant,
                kind="arm.group.delete",
            )
        if failure == "terminal":

            async def invalid(*args):
                raise ValueError("Injected local operation failure")

            from mockazure.jobs.repository import fail_job
            from mockazure.providers.arm import failure as failure_module

            async def evidence_unavailable(*args, **kwargs):
                raise RuntimeError("Injected evidence storage outage")

            with patch.object(failure_module, "append_source", evidence_unavailable):
                with pytest.raises(RuntimeError):
                    async with database.sessions.begin() as session:
                        await fail_job(
                            session, claim, error_code="ExportFailed", now=now, terminal=True
                        )
            async with database.sessions() as session:
                assert (await session.get(Job, claim.id)).state == "running"
            with patch.dict(HANDLERS, {"arm.group.delete": invalid}):
                assert (await dispatch_job(database, claim, now)).state == "failed"
        else:
            for attempt in (1, 2):
                async with database.sessions.begin() as session:
                    claim = await claim_job(
                        session,
                        owner=uuid4(),
                        now=now + timedelta(seconds=2 * attempt),
                        lease_seconds=1,
                        tenant_id=tenant,
                        kind="arm.group.delete",
                    )
                assert claim.attempt == attempt + 1
            async with database.sessions.begin() as session:
                assert (
                    await claim_job(
                        session,
                        owner=uuid4(),
                        now=now + timedelta(seconds=6),
                        lease_seconds=1,
                        tenant_id=tenant,
                        kind="arm.group.delete",
                    )
                    is None
                )
        async with database.sessions() as session:
            job = await session.get(Job, claim.id)
            assert job.state == "failed"
            assert await session.get(ArmResource, (tenant, scope.lower())) is not None
            facts = list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "arm"
                    )
                )
            )
            assert len(facts) == 1
            assert facts[0].facts["status"] == "Failed"
            assert facts[0].facts["correlation_id"] == claim.payload["correlation_id"]
            assert facts[0].facts["before"] == facts[0].facts["after"]
        # Projection remains independently retryable and never invents a success.
        drained = await client.post(evidence + "/drain", json={"max_jobs": 10})
        assert drained.status_code == 200 and drained.json()["failed"] == 0
        async with database.sessions() as session:
            rows = list(
                await session.scalars(
                    select(EvidenceVersion).where(
                        EvidenceVersion.tenant_id == tenant, EvidenceVersion.family == "activityLog"
                    )
                )
            )
            assert len(rows) == 1 and rows[0].payload["status"]["value"] == "Failed"
        polled = await client.get(started.headers["Location"], headers=headers)
        assert polled.status_code == 409
        assert polled.json()["error"]["code"] == (
            "ArmOperationFailed" if failure == "terminal" else "WorkerLeaseExpired"
        )

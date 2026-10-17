from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from mockazure.db.job_model import Job
from mockazure.evidence.models import EvidenceSource
from mockazure.identity.models import ServicePrincipal
from mockazure.jobs.dispatch import dispatch_job
from mockazure.jobs.repository import claim_job
from mockazure.providers.arm.models import ArmResource, RoleAssignment
from tests.integration.graph_support import graph_client, registered_app
from tests.integration.test_arm_authorization import assign
from tests.integration.test_arm_inventory import access
from tests.integration.test_arm_state import seed_arm


async def test_arm_delete_is_fenced_durable_and_scoped(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, scope, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(client, base, sp, f"/subscriptions/{sub}", "Owner")
        scoped = await assign(client, base, sp, scope, "Reader")
        await client.put(base + "/resource-groups/labother", json={"location": "eastus"})
        args = {"api-version": "2021-04-01"}
        deleted = await client.delete(scope, params=args, headers=headers)
        assert deleted.status_code == 202, deleted.text
        link = deleted.headers["Location"]
        assert deleted.headers["Retry-After"] == "1"
        repeated = await client.delete(scope, params=args, headers=headers)
        assert repeated.status_code == 202 and repeated.headers["Location"] == link
        assert (await client.get(link, headers=headers)).status_code == 202
        assert (
            await client.post(base + "/resources", json={"resources": [resource]})
        ).status_code == 409
        assert (
            await client.patch(scope, params=args, headers=headers, json={"tags": {"New": "Tag"}})
        ).status_code == 409
        no_access = await registered_app(client, tenant)
        assert (
            await client.get(link, headers=await access(database, settings, tenant, no_access))
        ).status_code == 403
        async with database.sessions() as session:
            assert not list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "arm"
                    )
                )
            )
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            stale = await claim_job(
                session,
                owner=uuid4(),
                now=now,
                lease_seconds=1,
                tenant_id=tenant,
                kind="arm.group.delete",
            )
        assert (await dispatch_job(database, stale, now + timedelta(seconds=2))).state == "stale"
        async with database.sessions.begin() as session:
            recovered = await claim_job(
                session,
                owner=uuid4(),
                now=now + timedelta(seconds=2),
                lease_seconds=30,
                tenant_id=tenant,
                kind="arm.group.delete",
            )
        assert recovered.epoch > stale.epoch
        # A lease lost after deleting rows and appending the fact rolls back both.
        from unittest.mock import patch

        from mockazure.providers.arm import operations

        original_append = operations.append_source
        expired = False

        async def expire_after_append(*args, **kwargs):
            nonlocal expired
            result = await original_append(*args, **kwargs)
            expired = True
            return result

        with (
            patch.object(operations, "append_source", expire_after_append),
            patch.object(operations, "monotonic", lambda: 100.0 if expired else 0.0),
        ):
            result = await dispatch_job(database, recovered, now + timedelta(seconds=2))
            assert result.state == "stale"
        async with database.sessions() as session:
            assert await session.get(ArmResource, (tenant, resource["id"].lower())) is not None
            assert not list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "arm"
                    )
                )
            )
        # Import and assignment writes wait behind deletion, then see the missing group.
        import asyncio

        entered, release = asyncio.Event(), asyncio.Event()

        async def pause_before_fact(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original_append(*args, **kwargs)

        with patch.object(operations, "append_source", pause_before_fact):
            worker = asyncio.create_task(
                dispatch_job(database, recovered, now + timedelta(seconds=2))
            )
            await asyncio.wait_for(entered.wait(), 5)
            importing = asyncio.create_task(
                client.post(base + "/resources", json={"resources": [resource]})
            )
            assigning = asyncio.create_task(
                client.put(
                    base + "/role-assignments/" + str(uuid4()),
                    json={
                        "principal_id": str(sp),
                        "principal_type": "ServicePrincipal",
                        "scope": scope,
                        "role": "Owner",
                    },
                )
            )
            try:
                await asyncio.sleep(0.05)
                assert not importing.done() and not assigning.done()
            finally:
                release.set()
            assert (await asyncio.wait_for(worker, 5)).state == "succeeded"
            assert (await importing).status_code == 404
            assert (await assigning).status_code == 404
        assert (
            await dispatch_job(database, recovered, now + timedelta(seconds=2))
        ).state == "stale"
        assert (await client.get(link, headers=headers)).status_code == 200
        assert (await client.get(scope, params=args, headers=headers)).status_code == 404
        assert (await client.get(scope + "other", params=args, headers=headers)).status_code == 200
        async with database.sessions() as session:
            assert await session.get(ArmResource, (tenant, resource["id"].lower())) is None
            from uuid import UUID

            assert await session.get(RoleAssignment, UUID(scoped["name"])) is None
            facts = list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "arm"
                    )
                )
            )
            assert len(facts) == 1
            assert facts[0].facts["status"] == "Succeeded"
            assert facts[0].facts["operation"].endswith("/delete")
            job = await session.get(Job, recovered.id)
            assert facts[0].facts["correlation_id"] == job.payload["correlation_id"]


async def test_arm_delete_reset_cannot_complete_stale_work(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, scope, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(client, base, sp, f"/subscriptions/{sub}", "Owner")
        response = await client.delete(scope, params={"api-version": "2021-04-01"}, headers=headers)
        assert response.status_code == 202, response.text
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            claim = await claim_job(
                session,
                owner=uuid4(),
                now=now,
                lease_seconds=30,
                tenant_id=tenant,
                kind="arm.group.delete",
            )
        from mockazure.evidence.cleanup import reset_evidence
        from mockazure.evidence.repository import lock_state

        async with database.sessions.begin() as session:
            state = await lock_state(session, tenant)
            generation = state.generation
        async with database.sessions.begin() as session:
            await reset_evidence(session, tenant, generation)
        assert (await dispatch_job(database, claim, now)).state == "failed"
        assert (await client.get(response.headers["Location"], headers=headers)).status_code == 409
        async with database.sessions() as session:
            group = await session.get(ArmResource, (tenant, scope.lower()))
            assert group is not None and group.provisioning_state == "Succeeded"
            assert not list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "arm"
                    )
                )
            )

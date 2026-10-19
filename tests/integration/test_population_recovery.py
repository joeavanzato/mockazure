from datetime import UTC, datetime, timedelta
from uuid import uuid4

from mockazure.jobs.repository import claim_job
from mockazure.population.planner import preview
from mockazure.population.repository import cancel_run, resume_run, run_view, submit_run
from mockazure.population.runner import process_population
from tests.integration.identity_support import identity_client
from tests.integration.test_population_runs import drive, request_for


async def test_cancel_resume_and_stale_worker_fencing(database, settings):
    async with identity_client(database, settings) as (_, tenant, *_):
        p = await preview(database, request_for(tenant), datetime.now(UTC))
        run = await submit_run(database, p.id, p.digest, datetime.now(UTC))
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            old = await claim_job(
                session, owner=uuid4(), now=now, lease_seconds=30, kind="population.apply"
            )
        assert old
        first = await process_population(database, old, now)
        assert first.state == "pending"
        assert (await process_population(database, old, now)).state == "stale"
        await cancel_run(database, run.id)
        assert (await run_view(database, run.id)).state == "cancelled"
        await resume_run(database, run.id)
        assert (await drive(database, run.id)).state == "succeeded"


async def test_expired_preview_is_rejected(database, settings):
    import pytest

    from mockazure.admin.errors import AdminError

    async with identity_client(database, settings) as (_, tenant, *_):
        p = await preview(database, request_for(tenant), datetime.now(UTC) - timedelta(hours=2))
        with pytest.raises(AdminError):
            await submit_run(database, p.id, p.digest, datetime.now(UTC))


async def developer_request(database, tenant):
    from mockazure.population.planner import RunRequest

    return RunRequest.model_validate(
        {
            "bundle_id": "builtin",
            "template": "developer-lab",
            "target": {"existing_tenant_id": str(tenant)},
            "dataset_namespace": "developer",
            "parameters": {"shared.users": 2, "shared.referenceTime": "2026-10-07T00:00:00Z"},
        }
    )


async def test_unknown_non_idempotent_response_is_never_repeated(database, settings, monkeypatch):
    from mockazure.population.actions import PublicActions
    from mockazure.population.identity import resolve_identity
    from mockazure.population.transport import LocalTransport

    async with identity_client(database, settings) as (client, tenant, *_):

        async def transport(self, context, identity):
            return LocalTransport(context, await resolve_identity(context, identity), client=client)

        monkeypatch.setattr(PublicActions, "transport", transport)
        original, writes = PublicActions.apply, []

        async def lost_response(self, ctx, mutations):
            result = await original(self, ctx, mutations)
            if self.base.describe().key == "mail.events":
                writes.append(result.items[0].id)
                raise TimeoutError("Injected response loss")
            return result

        monkeypatch.setattr(PublicActions, "apply", lost_response)
        p = await preview(database, await developer_request(database, tenant), datetime.now(UTC))
        run = await submit_run(database, p.id, p.digest, datetime.now(UTC))
        outcome = await drive(database, run.id, limit=300)
        assert outcome.state == "needs-attention", outcome
        assert len(writes) == 1
        monkeypatch.setattr(PublicActions, "apply", original)
        await resume_run(database, run.id)
        assert (await drive(database, run.id, limit=300)).state == "needs-attention"
        assert len(writes) == 1


async def test_completed_step_is_rechecked_before_skip(database, settings, monkeypatch):
    from uuid import UUID

    from mockazure.population.actions import PublicActions
    from mockazure.population.catalog import get_adapter
    from mockazure.population.identity import resolve_identity
    from mockazure.population.transport import LocalTransport
    from mockazure.population.types import AdapterContext, Mutation, ResourceQuery

    async with identity_client(database, settings) as (client, tenant, *_):

        async def transport(self, context, identity):
            return LocalTransport(context, await resolve_identity(context, identity), client=client)

        monkeypatch.setattr(PublicActions, "transport", transport)
        request = await developer_request(database, tenant)
        p = await preview(database, request, datetime.now(UTC))
        run = await submit_run(database, p.id, p.digest, datetime.now(UTC))
        assert (await drive(database, run.id, limit=300)).state == "succeeded"
        ctx = AdapterContext(database, settings, UUID(tenant))
        adapter = get_adapter("security.alerts_v2")
        row = (await adapter.browse(ctx, ResourceQuery(limit=1))).items[0]
        row = (await adapter.browse(ctx, ResourceQuery(identifier=row.id))).items[0]
        await adapter.apply(
            ctx,
            [
                Mutation(
                    operation="import",
                    logical_key="external",
                    expected_version=row.version,
                    payload={**row.properties, "title": "Externally changed"},
                )
            ],
        )
        p = await preview(database, request, datetime.now(UTC))
        repeat = await submit_run(database, p.id, p.digest, datetime.now(UTC))
        assert (await drive(database, repeat.id, limit=300)).state == "needs-attention"


async def test_exhausted_worker_lease_releases_population_slot(database, settings):
    from mockazure.db.job_model import Job
    from mockazure.population.models import PopulationRun

    async with identity_client(database, settings) as (_, tenant, *_):
        p = await preview(database, request_for(tenant), datetime.now(UTC))
        run = await submit_run(database, p.id, p.digest, datetime.now(UTC))
        async with database.sessions.begin() as session:
            stored = await session.get(PopulationRun, run.id)
            job = await session.get(Job, stored.job_id)
            job.state, job.attempts, job.lease_owner = "running", 3, uuid4()
            job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        async with database.sessions.begin() as session:
            await claim_job(session, owner=uuid4(), now=datetime.now(UTC), lease_seconds=30)
        assert (await run_view(database, run.id)).state == "needs-attention"

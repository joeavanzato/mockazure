from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from mockazure.admin.errors import AdminError
from mockazure.db.models import Tenant
from mockazure.population.models import PopulationRun
from mockazure.population.planner import RunRequest, preview
from mockazure.population.repository import export_manifest, run_view, save_bundle, submit_run
from mockazure.profiles.builtin import builtin_bundle
from tests.integration.identity_support import identity_client


def request_for(tenant=None, *, namespace="alpha"):
    return RunRequest.model_validate(
        {
            "bundle_id": "builtin",
            "template": "volume-lab",
            "target": {"existing_tenant_id": str(tenant)}
            if tenant
            else {
                "create_tenant": {
                    "slug": "new-lab",
                    "display_name": "New lab",
                    "domain": "new.test",
                    "licences": ["E5"],
                }
            },
            "dataset_namespace": namespace,
            "parameters": {
                "shared.users": 3,
                "volume-signins.records": 5,
                "shared.referenceTime": "2026-10-07T00:00:00Z",
            },
        }
    )


async def drive(database, run_id, *, limit=80):
    from mockazure.jobs.worker import run_cycle

    for _ in range(limit):
        view = await run_view(database, run_id)
        if view.state in {"succeeded", "failed", "needs-attention", "cancelled"}:
            return view
        await run_cycle(database, owner=uuid4(), now=datetime.now(UTC) + timedelta(seconds=2))
    raise AssertionError("Population did not reach a terminal state")


async def test_new_tenant_submit_is_idempotent(database, settings):
    from mockazure.identity.keys import initialize_identity

    await initialize_identity(settings, database)
    p = await preview(database, request_for(), datetime.now(UTC))
    async with database.sessions() as session:
        assert await session.get(Tenant, p.tenant_id) is None
    first = await submit_run(database, p.id, p.digest, datetime.now(UTC))
    second = await submit_run(database, p.id, p.digest, datetime.now(UTC))
    assert first.id == second.id
    assert (await drive(database, first.id)).state == "succeeded"
    again = await preview(database, request_for(first.tenant_id), datetime.now(UTC))
    repeated = await submit_run(database, again.id, again.digest, datetime.now(UTC))
    outcome = await drive(database, repeated.id)
    assert outcome.state == "succeeded"
    assert outcome.progress["created"] == 0


async def test_stale_preview_and_same_tenant_slot(database, settings):
    async with identity_client(database, settings) as (_, tenant, *_):
        p = await preview(database, request_for(tenant), datetime.now(UTC))
        async with database.sessions.begin() as session:
            row = await session.get(Tenant, p.tenant_id)
            row.display_name = "Changed"
        with pytest.raises(AdminError) as error:
            await submit_run(database, p.id, p.digest, datetime.now(UTC))
        assert error.value.status == 409
        fresh = await preview(database, request_for(tenant), datetime.now(UTC))
        await submit_run(database, fresh.id, fresh.digest, datetime.now(UTC))
        other = await preview(database, request_for(tenant, namespace="beta"), datetime.now(UTC))
        with pytest.raises(AdminError):
            await submit_run(database, other.id, other.digest, datetime.now(UTC))


async def test_manifest_and_ground_truth_exclude_credentials(database, settings):
    async with identity_client(database, settings) as (_, tenant, *_):
        p = await preview(database, request_for(tenant), datetime.now(UTC))
        run = await submit_run(database, p.id, p.digest, datetime.now(UTC))
        assert (await drive(database, run.id)).state == "succeeded"
        manifest = await export_manifest(database, run.id)
        serialized = str(manifest).lower()
        for forbidden in (
            "encrypted_secret",
            "authorization",
            "verifier",
            "password",
            "synthetic-",
        ):
            assert forbidden not in serialized
        assert manifest["source_digest"] == p.resolved_digest


async def test_profile_redefinition_does_not_change_running_snapshot(database, settings):
    async with identity_client(database, settings) as (_, tenant, *_):
        saved = await save_bundle(database, builtin_bundle())
        request = request_for(tenant).model_copy(update={"bundle_id": str(saved["id"])})
        p = await preview(database, request, datetime.now(UTC))
        run = await submit_run(database, p.id, p.digest, datetime.now(UTC))
        changed = builtin_bundle()
        changed.documents["volume-lab"].parameters["shared.users"] = 100
        revised = await save_bundle(database, changed, bundle_id=saved["id"], expected_revision=1)
        assert revised["revision"] == 2
        async with database.sessions() as session:
            stored = await session.get(PopulationRun, run.id)
            assert stored.resolved_digest == p.resolved_digest
        assert (await drive(database, run.id)).state == "succeeded"


async def test_default_reference_time_is_frozen_for_namespace_replays(database, settings):
    async with identity_client(database, settings) as (_, tenant, *_):
        request = request_for(tenant)
        request.parameters.pop("shared.referenceTime")
        first = await preview(database, request, datetime.now(UTC))
        run = await submit_run(database, first.id, first.digest, datetime.now(UTC))
        assert (await drive(database, run.id)).state == "succeeded"
        second = await preview(database, request, datetime.now(UTC) + timedelta(hours=1))
        assert second.resolved_digest == first.resolved_digest


async def test_two_tenants_can_hold_independent_worker_claims(database, settings):
    from mockazure.jobs.repository import claim_job
    from mockazure.population.runner import process_population

    async with identity_client(database, settings) as (_, tenant, *_):
        p = await preview(database, request_for(tenant), datetime.now(UTC))
        first = await submit_run(database, p.id, p.digest, datetime.now(UTC))
        p = await preview(database, request_for(), datetime.now(UTC))
        second = await submit_run(database, p.id, p.digest, datetime.now(UTC))
        async with database.sessions.begin() as session:
            one = await claim_job(
                session,
                owner=uuid4(),
                now=datetime.now(UTC),
                lease_seconds=30,
                kind="population.apply",
            )
        async with database.sessions.begin() as session:
            two = await claim_job(
                session,
                owner=uuid4(),
                now=datetime.now(UTC),
                lease_seconds=30,
                kind="population.apply",
            )
        assert {one.tenant_id, two.tenant_id} == {first.tenant_id, second.tenant_id}
        assert (await process_population(database, one, datetime.now(UTC))).state == "pending"
        assert (await process_population(database, two, datetime.now(UTC))).state == "pending"

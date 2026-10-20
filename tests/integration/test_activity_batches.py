from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from mockazure.db.job_model import Job
from mockazure.jobs.repository import claim_job
from mockazure.population.actions import PublicActions
from mockazure.population.identity import resolve_identity
from mockazure.population.models import PopulationRun, PopulationStep
from mockazure.population.planner import RunRequest, preview
from mockazure.population.protection import unseal
from mockazure.population.repository import (
    cancel_run,
    export_manifest,
    resume_run,
    run_view,
    save_bundle,
    submit_run,
)
from mockazure.population.runner import process_population
from mockazure.population.transport import LocalTransport
from mockazure.profiles.builtin import builtin_bundle
from tests.integration.identity_support import identity_client
from tests.integration.test_population_runs import drive


@asynccontextmanager
async def activity_lab(database, settings, monkeypatch, *, count=3, pacing=0):
    async with identity_client(database, settings) as (client, tenant, *_):

        async def transport(self, context, identity):
            return LocalTransport(context, await resolve_identity(context, identity), client=client)

        monkeypatch.setattr(PublicActions, "transport", transport)
        bundle = builtin_bundle()
        template = bundle.documents["developer-lab"]
        template.dataProfiles = ["exchange-mail-and-audits"]
        layer = bundle.documents["exchange-mail-and-audits"]
        layer.steps = {
            k: v
            for k, v in layer.steps.items()
            if k in {"mail-settings", "mailbox", "mail-folders", "calendar"}
        }
        saved = await save_bundle(database, bundle)
        request = RunRequest.model_validate(
            {
                "bundle_id": str(saved["id"]),
                "template": "developer-lab",
                "target": {"existing_tenant_id": tenant},
                "dataset_namespace": "activity",
                "parameters": {"shared.users": 2, "shared.referenceTime": "2001-10-07T00:00:00Z"},
                "activity": {"recipe_step": "calendar", "action_count": count, "pacing_ms": pacing},
            }
        )
        planned = await preview(database, request, datetime.now(UTC))
        run = await submit_run(database, planned.id, planned.digest, datetime.now(UTC))
        yield client, run


async def first_action(database, run):
    for _ in range(30):
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            claim = await claim_job(
                session,
                owner=uuid4(),
                now=now + timedelta(seconds=1),
                lease_seconds=30,
                kind="population.apply",
            )
        if claim:
            await process_population(database, claim, now + timedelta(seconds=1))
        async with database.sessions() as session:
            step = await session.scalar(
                select(PopulationStep).where(
                    PopulationStep.run_id == run.id, PopulationStep.key == "calendar"
                )
            )
            if step.offset == 1:
                return
        view = await run_view(database, run.id)
        assert view.state not in {"failed", "needs-attention"}, view
    raise AssertionError("First activity action was not reached")


async def test_batch_finishes_at_exact_count(database, settings, monkeypatch):
    async with activity_lab(database, settings, monkeypatch, count=5) as (_, run):
        result = await drive(database, run.id)
        assert result.state == "succeeded", result
        assert result.activity == {
            "recipe_step": "calendar",
            "completed": 5,
            "total": 5,
            "pacing_ms": 0,
            "reference_time": "2001-10-07T00:00:00Z",
        }
        manifest = await export_manifest(database, run.id)
        assert manifest["activity"]["completed"] == 5
        async with database.sessions() as session:
            from mockazure.calendar.models import CalendarEvent

            events = list(
                await session.scalars(
                    select(CalendarEvent).where(CalendarEvent.tenant_id == run.tenant_id)
                )
            )
        assert len(events) == 5
        assert len({e.id for e in events}) == 5


async def test_cancel_stops_future_actions_and_resume_preserves_recipe_and_clock(
    database,
    settings,
    monkeypatch,
):
    async with activity_lab(database, settings, monkeypatch, count=4) as (_, run):
        await first_action(database, run)
        async with database.sessions() as session:
            snapshot = unseal(database, (await session.get(PopulationRun, run.id)).encrypted)
        cancelled = await cancel_run(database, run.id)
        assert cancelled.state == "cancelled" and cancelled.activity["completed"] == 1
        assert (await drive(database, run.id)).state == "cancelled"
        await resume_run(database, run.id)
        done = await drive(database, run.id)
        assert done.state == "succeeded" and done.activity["completed"] == 4
        async with database.sessions() as session:
            assert (
                unseal(database, (await session.get(PopulationRun, run.id)).encrypted) == snapshot
            )
            from mockazure.calendar.models import CalendarEvent

            assert (
                len(
                    list(
                        await session.scalars(
                            select(CalendarEvent).where(CalendarEvent.tenant_id == run.tenant_id)
                        )
                    )
                )
                == 4
            )


async def test_pacing_releases_worker_lease(database, settings, monkeypatch):
    async with activity_lab(database, settings, monkeypatch, pacing=60000) as (_, run):
        await first_action(database, run)
        async with database.sessions() as session:
            stored = await session.get(PopulationRun, run.id)
            job = await session.get(Job, stored.job_id)
            assert job.state == "pending" and job.lease_owner is None and job.lease_until is None
            assert job.available_at > datetime.now(UTC) + timedelta(seconds=55)
        async with database.sessions.begin() as session:
            assert (
                await claim_job(
                    session,
                    owner=uuid4(),
                    now=datetime.now(UTC),
                    lease_seconds=30,
                    kind="population.apply",
                )
                is None
            )


async def test_uncertain_send_requires_attention_not_duplicate(database, settings, monkeypatch):
    original, sent = PublicActions.apply, []

    async def lose_response(self, ctx, mutations):
        result = await original(self, ctx, mutations)
        if self.base.describe().key == "mail.events":
            sent.extend(item.id for item in result.items)
            raise TimeoutError("Response lost after committed public POST")
        return result

    monkeypatch.setattr(PublicActions, "apply", lose_response)
    async with activity_lab(database, settings, monkeypatch) as (_, run):
        assert (await drive(database, run.id)).state == "needs-attention"
        assert len(sent) == 1
        monkeypatch.setattr(PublicActions, "apply", original)
        await resume_run(database, run.id)
        assert (await drive(database, run.id)).state == "needs-attention"
        assert len(sent) == 1


async def test_evidence_time_does_not_change_oauth_expiry(database, settings, monkeypatch):
    import jwt

    expiries = []
    async with activity_lab(database, settings, monkeypatch, count=1) as (client, run):

        async def observe(response):
            if response.request.url.path.endswith("/oauth2/v2.0/token"):
                await response.aread()
                expiries.append(
                    jwt.decode(
                        response.json()["access_token"], options={"verify_signature": False}
                    )["exp"]
                )

        client.event_hooks["response"].append(observe)
        assert (await drive(database, run.id)).state == "succeeded"
        assert expiries and all(value > datetime.now(UTC).timestamp() for value in expiries)


@pytest.mark.parametrize("count,pacing", [(0, 0), (100001, 0), (1, -1), (1, 60001)])
def test_activity_bounds_are_strict(count, pacing):
    from mockazure.population.activity_models import ActivityBatch

    with pytest.raises(ValueError):
        ActivityBatch(recipe_step="calendar", action_count=count, pacing_ms=pacing)

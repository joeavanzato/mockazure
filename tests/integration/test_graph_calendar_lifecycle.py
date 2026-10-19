"""Calendar lifecycle and workers retain state while retiring stale authority."""

import copy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError

from mockazure.db.migrations import configuration
from mockazure.jobs.dispatch import dispatch_job
from mockazure.jobs.repository import claim_job
from tests.integration.graph_support import graph_client
from tests.integration.test_graph_calendar_workflows import event_body, meeting_worker
from tests.integration.test_graph_send_workflows import setup_mail
from tests.integration.workflow_support import headers_for


async def test_calendar_export_import_reset_invalidates_pending_work(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        attendee, _, _ = await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        root = f"/v1.0/users/{owner}/events"
        created = await client.post(root, headers=write, json=event_body())
        assert created.status_code == 201, created.text
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            claim = await claim_job(
                session,
                owner=uuid4(),
                now=now,
                lease_seconds=30,
                tenant_id=tenant,
                kind="calendar.meeting",
            )
        cloud = f"/_mock/tenants/{tenant}/cloud/state"
        snapshot = await client.get(cloud)
        assert snapshot.status_code == 200, snapshot.text
        saved = snapshot.json()
        assert saved["calendars"]["owners"][0]["events"][0]["id"] == created.json()["id"]
        broken = copy.deepcopy(saved)
        broken["calendars"]["owners"][0]["events"][0]["calendar_id"] = "missing"
        bad = await client.put(cloud, json=broken)
        assert bad.status_code == 422, bad.text
        assert (
            await client.get(root + "/" + created.json()["id"], headers=write)
        ).status_code == 200
        restored = await client.put(cloud, json=saved)
        assert restored.status_code == 200, restored.text
        assert (await dispatch_job(database, claim, now)).state == "stale"
        assert (await client.get(f"/v1.0/users/{attendee}/events", headers=write)).json()[
            "value"
        ] == []
        current = (await client.get(cloud)).json()
        reset = await client.post(
            f"/_mock/tenants/{tenant}/cloud/reset",
            json={"expected_generation": current["generation"]},
        )
        assert reset.status_code == 200, reset.text
        assert (await client.get(root, headers=write)).json()["value"] == []


async def test_calendar_upgrade_and_lossy_downgrade(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        await setup_mail(client, database, settings, tenant, app, owner)
        engine = create_engine(settings.database_url.get_secret_value())
        try:
            config = configuration()
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "068")
                command.upgrade(config, "head")
            write = await headers_for(
                database, settings, tenant, app, None, {"Calendars.ReadWrite"}
            )
            body = event_body()
            body["attendees"] = []
            created = await client.post(f"/v1.0/users/{owner}/events", headers=write, json=body)
            assert created.status_code == 201, created.text
            await database.engine.dispose()
            assert (
                await client.get(
                    f"/v1.0/users/{owner}/events/{created.json()['id']}", headers=write
                )
            ).json()["subject"] == body["subject"]
            with pytest.raises(DBAPIError, match="Reset calendar state before downgrade"):
                with engine.begin() as connection:
                    config.attributes["connection"] = connection
                    command.downgrade(config, "068")
        finally:
            engine.dispose()


async def test_decline_and_suppressed_response(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        attendee, _, _ = await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        root = f"/v1.0/users/{owner}/events"
        created = await client.post(root, headers=write, json=event_body())
        assert created.status_code == 201, created.text
        await meeting_worker(database, tenant)
        attendee_root = f"/v1.0/users/{attendee}/events"
        item = (await client.get(attendee_root, headers=write)).json()["value"][0]
        response = await client.post(
            attendee_root + "/" + item["id"] + "/accept",
            headers=write,
            json={"sendResponse": False},
        )
        assert response.status_code == 202 and response.content == b""
        organizer = (await client.get(root + "/" + created.json()["id"], headers=write)).json()
        assert organizer["attendees"][0]["status"]["response"] == "none"
        declined = await client.post(
            attendee_root + "/" + item["id"] + "/decline",
            headers=write,
            json={"sendResponse": True},
        )
        assert declined.status_code == 202 and declined.content == b""
        await meeting_worker(database, tenant)
        organizer = (await client.get(root + "/" + created.json()["id"], headers=write)).json()
        assert organizer["attendees"][0]["status"]["response"] == "declined"
        assert (
            await client.get(attendee_root + "/" + item["id"], headers=write)
        ).status_code == 404


async def test_calendar_worker_lease_rolls_back_both_event_and_message(
    database, settings, monkeypatch
):
    from mockazure.calendar import meetings

    async with graph_client(database, settings) as (client, tenant, app, owner):
        attendee, mail_read, _ = await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        assert (
            await client.post(f"/v1.0/users/{owner}/events", headers=write, json=event_body())
        ).status_code == 201
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            claim = await claim_job(
                session,
                owner=uuid4(),
                now=now,
                lease_seconds=30,
                tenant_id=tenant,
                kind="calendar.meeting",
            )
        clock = [0]
        original = meetings.local_message

        async def expire(*args, **kwargs):
            await original(*args, **kwargs)
            clock[0] = 60

        with monkeypatch.context() as patch:
            patch.setattr(meetings, "monotonic", lambda: clock[0])
            patch.setattr(meetings, "local_message", expire)
            assert (await dispatch_job(database, claim, now)).state == "stale"
        assert (await client.get(f"/v1.0/users/{attendee}/events", headers=write)).json()[
            "value"
        ] == []
        assert (await client.get(f"/v1.0/users/{attendee}/messages", headers=mail_read)).json()[
            "value"
        ] == []
        assert (await dispatch_job(database, claim, now)).state == "succeeded"


async def test_calendar_cancel_before_invitation_cannot_resurrect_meeting(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        attendee, _, _ = await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        root = f"/v1.0/users/{owner}/events"
        created = await client.post(root, headers=write, json=event_body())
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            first = await claim_job(
                session,
                owner=uuid4(),
                now=now,
                lease_seconds=30,
                tenant_id=tenant,
                kind="calendar.meeting",
            )
        assert (
            await client.post(root + "/" + created.json()["id"] + "/cancel", headers=write, json={})
        ).status_code == 202
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            cancel = await claim_job(
                session,
                owner=uuid4(),
                now=now,
                lease_seconds=30,
                tenant_id=tenant,
                kind="calendar.meeting",
            )
        assert (await dispatch_job(database, cancel, now)).state == "succeeded"
        assert (await dispatch_job(database, first, now)).state == "succeeded"
        events = (await client.get(f"/v1.0/users/{attendee}/events", headers=write)).json()["value"]
        assert all(e["isCancelled"] for e in events)


async def test_calendar_removed_attendee_update_cannot_cancel_newer_reinvite(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        attendee, _, _ = await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        root = f"/v1.0/users/{owner}/events"
        created = await client.post(root, headers=write, json=event_body())
        await meeting_worker(database, tenant)
        path = root + "/" + created.json()["id"]
        assert (await client.patch(path, headers=write, json={"attendees": []})).status_code == 200
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            removed = await claim_job(
                session,
                owner=uuid4(),
                now=now,
                lease_seconds=30,
                tenant_id=tenant,
                kind="calendar.meeting",
            )
        assert (
            await client.patch(path, headers=write, json={"attendees": event_body()["attendees"]})
        ).status_code == 200
        await meeting_worker(database, tenant)
        assert (await dispatch_job(database, removed, datetime.now(UTC))).state == "succeeded"
        events = (await client.get(f"/v1.0/users/{attendee}/events", headers=write)).json()["value"]
        assert len(events) == 1 and not events[0]["isCancelled"]


async def test_calendar_import_rebases_recipient_revisions_for_new_delivery(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        attendee, _, _ = await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        root = f"/v1.0/users/{owner}/events"
        created = await client.post(root, headers=write, json=event_body())
        await meeting_worker(database, tenant)
        path = root + "/" + created.json()["id"]
        for n in range(3):
            assert (
                await client.patch(path, headers=write, json={"subject": str(n)})
            ).status_code == 200
            await meeting_worker(database, tenant)
        cloud = f"/_mock/tenants/{tenant}/cloud/state"
        snapshot = (await client.get(cloud)).json()
        assert (await client.put(cloud, json=snapshot)).status_code == 200
        assert (
            await client.patch(path, headers=write, json={"subject": "New generation"})
        ).status_code == 200
        await meeting_worker(database, tenant)
        rows = (await client.get(f"/v1.0/users/{attendee}/events", headers=write)).json()["value"]
        assert rows[0]["subject"] == "New generation"


async def test_calendar_history_pruning_is_bounded_and_keeps_current_state(database, settings):
    from datetime import timedelta

    from sqlalchemy import func, select

    from mockazure.calendar.lifecycle import prune_calendars
    from mockazure.calendar.models import CalendarEventVersion

    async with graph_client(database, settings) as (client, tenant, app, owner):
        await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        body = event_body()
        body["attendees"] = []
        root = f"/v1.0/users/{owner}/events"
        created = await client.post(root, headers=write, json=body)
        path = root + "/" + created.json()["id"]
        for n in range(3):
            assert (
                await client.patch(path, headers=write, json={"subject": str(n)})
            ).status_code == 200
        async with database.sessions.begin() as session:
            count = (
                select(func.count())
                .select_from(CalendarEventVersion)
                .where(CalendarEventVersion.tenant_id == tenant)
            )
            assert await session.scalar(count) == 4
            assert (
                await prune_calendars(session, tenant, datetime.now(UTC) + timedelta(days=2), 2)
                == 2
            )
            assert await session.scalar(count) == 2
            assert (
                await prune_calendars(session, tenant, datetime.now(UTC) + timedelta(days=2), 2)
                == 1
            )
            assert await session.scalar(count) == 1
        assert (await client.get(path, headers=write)).json()["subject"] == "2"

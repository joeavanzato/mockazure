"""Calendar windows, occurrence edits, availability and durable authenticated delta."""

from datetime import UTC, datetime
from uuid import UUID

from mockazure.identity.models import Application
from tests.integration.graph_support import graph_client
from tests.integration.test_graph_calendar_workflows import event_body, meeting_worker
from tests.integration.test_graph_send_workflows import setup_mail
from tests.integration.workflow_support import headers_for


def recurring():
    body = event_body()
    body.update(
        start={"dateTime": "2026-10-25T10:00:00", "timeZone": "Eastern Standard Time"},
        end={"dateTime": "2026-10-25T11:00:00", "timeZone": "Eastern Standard Time"},
        recurrence={
            "pattern": {"type": "weekly", "interval": 1, "daysOfWeek": ["sunday"]},
            "range": {"type": "numbered", "startDate": "2026-10-25", "numberOfOccurrences": 3},
        },
    )
    return body


async def collect(client, path, headers):
    values = []
    for _ in range(30):
        response = await client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()
        values += data["value"]
        if "@odata.nextLink" not in data:
            return values, data.get("@odata.deltaLink")
        path = data["@odata.nextLink"]
    raise AssertionError("Unbounded calendar paging")


async def test_calendar_dst_and_exceptions(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        attendee, _, _ = await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        root = f"/v1.0/users/{owner}"
        created = await client.post(root + "/events", headers=write, json=recurring())
        assert created.status_code == 201, created.text
        assert created.json()["type"] == "seriesMaster"
        path = root + "/events/" + created.json()["id"]
        window = "?startDateTime=2026-10-24T00:00:00Z&endDateTime=2026-11-10T00:00:00Z&$top=1"
        rows, _ = await collect(client, root + "/calendarView" + window, write)
        assert [r["start"]["dateTime"] for r in rows] == [
            "2026-10-25T14:00:00",
            "2026-11-01T15:00:00",
            "2026-11-08T15:00:00",
        ]
        ids = [r["id"] for r in rows]
        assert [
            r["id"] for r in (await collect(client, path + "/instances" + window, write))[0]
        ] == ids
        changed = await client.patch(
            root + "/events/" + ids[1],
            headers=write,
            json={
                "subject": "Exception review",
                "start": {"dateTime": "2026-11-01T12:00:00", "timeZone": "Eastern Standard Time"},
                "end": {"dateTime": "2026-11-01T13:00:00", "timeZone": "Eastern Standard Time"},
            },
        )
        assert changed.status_code == 200 and changed.json()["type"] == "exception", changed.text
        assert (await client.delete(root + "/events/" + ids[2], headers=write)).status_code == 204
        assert (
            await client.patch(path, headers=write, json={"subject": "Series renamed"})
        ).status_code == 200
        rows, _ = await collect(client, root + "/calendarView" + window, write)
        assert [r["id"] for r in rows] == ids[:2]
        assert [r["subject"] for r in rows] == ["Series renamed", "Exception review"]
        assert rows[1]["start"]["dateTime"] == "2026-11-01T17:00:00"
        await database.engine.dispose()
        assert (await client.get(root + "/events/" + ids[1], headers=write)).json()[
            "type"
        ] == "exception"
        for _ in range(4):
            await meeting_worker(database, tenant)
        copies, _ = await collect(client, f"/v1.0/users/{attendee}/calendarView" + window, write)
        assert [r["iCalUId"] for r in copies] == [r["iCalUId"] for r in rows]
        assert all(r["id"] not in ids for r in copies)


async def test_calendar_delta_reauthorization(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        _, _, _ = await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        write["Prefer"] = "odata.maxpagesize=1"
        root = f"/v1.0/users/{owner}"
        body = recurring()
        body["attendees"] = []
        created = await client.post(root + "/events", headers=write, json=body)
        assert created.status_code == 201, created.text
        delta = (
            root + "/calendarView/delta?startDateTime=2026-10-24T00:00:00Z"
            "&endDateTime=2026-11-10T00:00:00Z"
        )
        initial, link = await collect(client, delta, write)
        assert len(initial) == 3 and link
        changed_id, deleted_id = initial[0]["id"], initial[1]["id"]
        assert (
            await client.patch(
                root + "/events/" + changed_id, headers=write, json={"subject": "Changed"}
            )
        ).status_code == 200
        assert (
            await client.delete(root + "/events/" + deleted_id, headers=write)
        ).status_code == 204
        await database.engine.dispose()
        changes, next_link = await collect(client, link, write)
        assert {r["id"] for r in changes} == {changed_id, deleted_id}
        assert next(r for r in changes if r["id"] == deleted_id)["@removed"] == {
            "reason": "deleted"
        }
        assert (
            await client.get(next_link + "&startDateTime=2026-10-01T00:00:00Z", headers=write)
        ).status_code == 400
        assert (await collect(client, next_link, write))[0] == []
        async with database.sessions.begin() as session:
            application = await session.get(Application, UUID(str(app)))
            application.enabled = False
        assert (await client.get(next_link, headers=write)).status_code in {401, 403}


async def test_calendar_schedule_only_authorized_availability(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        _, _, _ = await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        basic = await headers_for(
            database, settings, tenant, app, None, {"Calendars.ReadBasic.All"}
        )
        root = f"/v1.0/users/{owner}"
        body = event_body()
        body["attendees"] = []
        body["sensitivity"] = "private"
        assert (await client.post(root + "/events", headers=write, json=body)).status_code == 201
        query = {
            "schedules": ["reader@contoso.test"],
            "startTime": {"dateTime": "2026-10-25T09:00:00", "timeZone": "UTC"},
            "endTime": {"dateTime": "2026-10-25T12:00:00", "timeZone": "UTC"},
            "availabilityViewInterval": 30,
        }
        result = await client.post(root + "/calendar/getSchedule", headers=basic, json=query)
        assert result.status_code == 200, result.text
        info = result.json()["value"][0]
        assert info["availabilityView"] == "002200"
        assert (
            "subject" not in info["scheduleItems"][0] and "location" not in info["scheduleItems"][0]
        )
        event = (await client.get(root + "/events", headers=write)).json()["value"][0]
        assert (
            await client.patch(
                root + "/events/" + event["id"],
                headers=write,
                json={"location": None, "showAs": None},
            )
        ).status_code == 200
        cleared = await client.post(root + "/calendar/getSchedule", headers=write, json=query)
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["value"][0]["availabilityView"] == "000000"
        query["availabilityViewInterval"] = 4
        assert (
            await client.post(root + "/calendar/getSchedule", headers=basic, json=query)
        ).status_code == 400


async def test_calendar_exception_can_move_between_neighbors_but_not_cross_them(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        root = f"/v1.0/users/{owner}"
        body = recurring()
        body["attendees"] = []
        created = await client.post(root + "/events", headers=write, json=body)
        rows, _ = await collect(
            client,
            root
            + "/calendarView?startDateTime=2026-10-24T00:00:00Z&endDateTime=2026-11-10T00:00:00Z",
            write,
        )
        path = root + "/events/" + rows[1]["id"]
        move = {
            "start": {"dateTime": "2026-11-03T10:00:00", "timeZone": "Eastern Standard Time"},
            "end": {"dateTime": "2026-11-03T11:00:00", "timeZone": "Eastern Standard Time"},
        }
        changed = await client.patch(path, headers=write, json=move)
        assert changed.status_code == 200, changed.text
        move["start"]["dateTime"] = "2026-11-08T10:00:00"
        move["end"]["dateTime"] = "2026-11-08T11:00:00"
        denied = await client.patch(path, headers=write, json=move)
        assert (
            denied.status_code == 400
            and denied.json()["error"]["code"] == "ErrorOccurrenceCrossingBoundary"
        )
        assert (await client.get(path, headers=write)).json()["start"][
            "dateTime"
        ] == "2026-11-03T15:00:00"
        cloud = f"/_mock/tenants/{tenant}/cloud/state"
        snapshot = (await client.get(cloud)).json()
        restored = await client.put(cloud, json=snapshot)
        assert restored.status_code == 200, restored.text
        assert (await client.get(path, headers=write)).json()["start"][
            "dateTime"
        ] == "2026-11-03T15:00:00"
        # Removing recurrence also retires now-inapplicable overrides atomically.
        assert (
            await client.patch(
                root + "/events/" + created.json()["id"], headers=write, json={"recurrence": None}
            )
        ).status_code == 200
        exported = await client.get(cloud)
        assert exported.status_code == 200, exported.text
        assert not exported.json()["calendars"]["owners"][0]["exceptions"]


async def test_calendar_delta_reset_expiry_and_primary_scope(database, settings):
    from datetime import timedelta

    async with graph_client(database, settings) as (client, tenant, app, owner):
        await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        root = f"/v1.0/users/{owner}"
        window = "?startDateTime=2026-10-24T00:00:00Z&endDateTime=2026-11-10T00:00:00Z"
        _, link = await collect(client, root + "/calendarView/delta" + window, write)
        assert (
            await client.get(root + "/calendar/calendarView/delta" + window, headers=write)
        ).status_code == 501
        assert (
            await client.get(root + "/calendarView/delta" + window + "&$select=id", headers=write)
        ).status_code == 400
        cloud = f"/_mock/tenants/{tenant}/cloud/state"
        exported = (await client.get(cloud)).json()
        assert (await client.put(cloud, json=exported)).status_code == 200
        assert (await client.get(link, headers=write)).status_code in {400, 410}
        _, link = await collect(client, root + "/calendarView/delta" + window, write)
        from sqlalchemy import update

        from mockazure.mail.message_models import MailContentCursor

        async with database.sessions.begin() as session:
            await session.execute(
                update(MailContentCursor)
                .where(MailContentCursor.tenant_id == tenant)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        assert (await client.get(link, headers=write)).status_code == 410


async def test_calendar_view_aliases_enforce_parent_scope(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        root = f"/v1.0/users/{owner}"
        group = (
            await client.post(root + "/calendarGroups", headers=write, json={"name": "Cases"})
        ).json()["id"]
        cal = (
            await client.post(
                root + "/calendarGroups/" + group + "/calendars",
                headers=write,
                json={"name": "Reviews"},
            )
        ).json()["id"]
        body = recurring()
        body["attendees"] = []
        event = (
            await client.post(root + "/calendars/" + cal + "/events", headers=write, json=body)
        ).json()["id"]
        window = "?startDateTime=2026-10-24T00:00:00Z&endDateTime=2026-11-10T00:00:00Z"
        assert (
            await client.get(
                root + "/calendar/events/" + event + "/instances" + window, headers=write
            )
        ).status_code == 404
        path = root + "/calendarGroups/" + group + "/calendars/" + cal
        assert len((await collect(client, path + "/calendarView" + window, write))[0]) == 3
        assert (
            len(
                (await collect(client, path + "/events/" + event + "/instances" + window, write))[0]
            )
            == 3
        )
        assert (
            await client.get(path.replace(group, "wrong") + "/calendarView" + window, headers=write)
        ).status_code == 404


async def test_native_sdk_delta_function_uri(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        await setup_mail(client, database, settings, tenant, app, owner)
        headers = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        root = f"/v1.0/users/{owner}"
        created = await client.post(root + "/events", headers=headers, json=event_body())
        assert created.status_code == 201, created.text
        path = (
            root + "/calendarView/delta()?startDateTime=2026-10-01T00:00:00Z"
            "&endDateTime=2026-11-01T00:00:00Z"
        )
        rows, link = await collect(client, path, headers)
        assert created.json()["id"] in {row["id"] for row in rows}
        assert "/delta()" in link
        assert (await client.get(link, headers=headers)).status_code == 200

"""Graph calendar API workflows use persisted state, actual permission checks and workers."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mockazure.jobs.dispatch import dispatch_job
from mockazure.jobs.repository import claim_job
from tests.integration.graph_support import graph_client
from tests.integration.test_graph_send_workflows import setup_mail
from tests.integration.workflow_support import headers_for


def event_body():
    return {
        "subject": "Evidence review",
        "body": {"contentType": "text", "content": "Private case details"},
        "start": {"dateTime": "2026-10-25T10:00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-10-25T11:00:00", "timeZone": "UTC"},
        "attendees": [{"type": "required", "emailAddress": {"address": "recipient@contoso.test"}}],
        "transactionId": "local-meeting-transaction",
    }


async def meeting_worker(database, tenant):
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
    assert claim is not None
    await database.engine.dispose()
    result = await dispatch_job(database, claim, now)
    assert result.state == "succeeded", result
    assert (await dispatch_job(database, claim, now)).state == "stale"


async def test_calendar_meeting_roundtrip(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        attendee, mail_read, _ = await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        root = f"/v1.0/users/{owner}"
        group = await client.post(root + "/calendarGroups", headers=write, json={"name": "Cases"})
        assert group.status_code == 201, group.text
        group_path = root + "/calendarGroups/" + group.json()["id"]
        assert (
            await client.patch(group_path, headers=write, json={"name": "Case reviews"})
        ).status_code == 200
        calendar = await client.post(
            group_path + "/calendars", headers=write, json={"name": "Investigations"}
        )
        assert calendar.status_code == 201, calendar.text
        cal = root + "/calendars/" + calendar.json()["id"]
        updated_cal = await client.patch(cal, headers=write, json={"color": "lightBlue"})
        assert updated_cal.status_code == 200
        assert updated_cal.json()["owner"]["address"] == "reader@contoso.test"
        created = await client.post(cal + "/events", headers=write, json=event_body())
        assert created.status_code == 201, created.text
        organizer = created.json()
        assert organizer["isOrganizer"] is True and organizer["type"] == "singleInstance"
        retried = await client.post(cal + "/events", headers=write, json=event_body())
        assert retried.status_code == 201 and retried.json()["id"] == organizer["id"]
        await meeting_worker(database, tenant)
        attendee_root = f"/v1.0/users/{attendee}"
        copies = await client.get(attendee_root + "/events", headers=write)
        assert copies.status_code == 200, copies.text
        attendee_event = copies.json()["value"][0]
        assert attendee_event["id"] != organizer["id"]
        assert attendee_event["iCalUId"] == organizer["iCalUId"]
        assert attendee_event["isOrganizer"] is False
        assert attendee_event["responseStatus"]["response"] == "notResponded"
        attendee_path = attendee_root + "/events/" + attendee_event["id"]
        organizer_path = cal + "/events/" + organizer["id"]
        invitations = await client.get(attendee_root + "/messages", headers=mail_read)
        assert invitations.status_code == 200
        assert len(invitations.json()["value"]) == 1
        assert invitations.json()["value"][0]["id"] != attendee_event["id"]
        assert invitations.json()["value"][0]["meetingMessageType"] == "meetingRequest"
        for action, status in [
            ("tentativelyAccept", "tentativelyAccepted"),
            ("accept", "accepted"),
        ]:
            response = await client.post(
                attendee_path + "/" + action,
                headers=write,
                json={"comment": "Local response", "sendResponse": True},
            )
            assert response.status_code == 202 and response.content == b"", response.text
            await meeting_worker(database, tenant)
            current = await client.get(organizer_path, headers=write)
            assert current.json()["attendees"][0]["status"]["response"] == status
        changed = await client.patch(
            organizer_path, headers=write, json={"subject": "Revised review"}
        )
        assert changed.status_code == 200
        await meeting_worker(database, tenant)
        assert (await client.get(attendee_path, headers=write)).json()[
            "subject"
        ] == "Revised review"
        denied = await client.post(attendee_path + "/cancel", headers=write, json={})
        assert denied.status_code == 400
        cancelled = await client.post(
            organizer_path + "/cancel", headers=write, json={"comment": "Cancelled"}
        )
        assert cancelled.status_code == 202 and cancelled.content == b""
        await meeting_worker(database, tenant)
        assert (await client.get(organizer_path, headers=write)).status_code == 404
        assert (await client.get(attendee_path, headers=write)).json()["isCancelled"] is True
        assert (await client.delete(attendee_path, headers=write)).status_code == 204
        assert (await client.delete(cal, headers=write)).status_code == 204
        assert (await client.delete(group_path, headers=write)).status_code == 204


async def test_calendar_permissions(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        other, _, mail_send = await setup_mail(client, database, settings, tenant, app, owner)
        root = f"/v1.0/users/{owner}"
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        basic = await headers_for(
            database, settings, tenant, app, None, {"Calendars.ReadBasic.All"}
        )
        delegated = await headers_for(
            database, settings, tenant, app, owner, {"Calendars.ReadWrite"}, kind="delegated"
        )
        payload = event_body()
        payload["attendees"] = []
        created = await client.post(root + "/events", headers=write, json=payload)
        assert created.status_code == 201, created.text
        path = root + "/events/" + created.json()["id"]
        read = await client.get(path, headers=basic)
        assert read.status_code == 200
        assert "body" not in read.json() and "bodyPreview" not in read.json()
        assert (await client.get(path + "?$select=id,body", headers=basic)).status_code == 403
        assert (
            await client.patch(path, headers=basic, json={"subject": "forbidden"})
        ).status_code == 403
        assert (await client.get(root + "/events", headers=mail_send)).status_code == 403
        assert (await client.get("/v1.0/me/events", headers=write)).status_code == 400
        assert (await client.get("/v1.0/me/events", headers=delegated)).status_code == 200
        assert (
            await client.get(f"/v1.0/users/{other}/events", headers=delegated)
        ).status_code == 403
        assert (
            await client.patch(
                path,
                headers=write,
                json={"organizer": {"emailAddress": {"address": "forged@contoso.test"}}},
            )
        ).status_code == 400
        assert (await client.get(path, headers=write)).json()["organizer"]["emailAddress"][
            "address"
        ] == "reader@contoso.test"


@pytest.mark.parametrize(
    "body",
    [
        {"start": {"dateTime": "not-a-date", "timeZone": "UTC"}},
        {"unknown": True},
        {"sendResponse": "false"},
    ],
)
async def test_calendar_invalid_payloads_are_explicit(database, settings, body):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        result = await client.post(f"/v1.0/users/{owner}/events", headers=write, json=body)
        assert result.status_code == 400, result.text


async def test_calendar_wire_preferences_and_snapshot_paging(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        root = f"/v1.0/users/{owner}"
        prefer = {
            **write,
            "Prefer": 'outlook.timezone="America/New_York", outlook.body-content-type="text"',
        }
        ids = set()
        for n in range(3):
            body = {**event_body(), "transactionId": str(n), "attendees": []}
            body["body"] = {"contentType": "html", "content": "<p>Private case</p>"}
            result = await client.post(root + "/events", headers=prefer, json=body)
            assert result.status_code == 201, result.text
            assert result.json()["start"] == {
                "dateTime": "2026-10-25T06:00:00",
                "timeZone": "America/New_York",
            }
            assert result.json()["body"]["contentType"] == "text"
            assert 'outlook.body-content-type="text"' in result.headers["Preference-Applied"]
            ids.add(result.json()["id"])
        page = await client.get(
            root + "/events?$top=1&$select=id,subject&$count=true", headers=write
        )
        assert page.status_code == 200 and page.json()["@odata.count"] == 3
        seen = set()
        while True:
            seen.update(v["id"] for v in page.json()["value"])
            assert all(set(v) == {"id", "subject"} for v in page.json()["value"])
            if "@odata.nextLink" not in page.json():
                break
            await database.engine.dispose()
            page = await client.get(page.json()["@odata.nextLink"], headers=write)
            assert page.status_code == 200, page.text
        assert seen == ids
        groups = await client.get(root + "/calendarGroups", headers=write)
        assert groups.json()["@odata.context"].endswith("#calendarGroups")
        path = root + "/events/" + next(iter(ids))
        old = await client.get(path, headers=write)
        patch = await client.patch(
            path, headers={**write, "If-Match": old.headers["ETag"]}, json={"subject": "Updated"}
        )
        assert patch.status_code == 200
        assert (
            await client.patch(
                path, headers={**write, "If-Match": old.headers["ETag"]}, json={"subject": "Stale"}
            )
        ).status_code == 412


async def test_calendar_attendee_patch_preserves_response(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        attendee, _, _ = await setup_mail(client, database, settings, tenant, app, owner)
        write = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        root = f"/v1.0/users/{owner}/events"
        created = await client.post(root, headers=write, json=event_body())
        await meeting_worker(database, tenant)
        other = f"/v1.0/users/{attendee}/events"
        copy = (await client.get(other, headers=write)).json()["value"][0]
        assert (
            await client.post(other + "/" + copy["id"] + "/accept", headers=write, json={})
        ).status_code == 202
        await meeting_worker(database, tenant)
        changed = await client.patch(
            root + "/" + created.json()["id"],
            headers=write,
            json={"attendees": event_body()["attendees"]},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["attendees"][0]["status"]["response"] == "accepted"


async def test_attendees_only_patch_does_not_notify_unchanged_attendees(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        attendee, _, _ = await setup_mail(client, database, settings, tenant, app, owner)
        headers = await headers_for(
            database, settings, tenant, app, None, {"Calendars.ReadWrite", "Mail.Read"}
        )
        root = f"/v1.0/users/{owner}/events"
        response = await client.post(root, headers=headers, json=event_body())
        assert response.status_code == 201, response.text
        await meeting_worker(database, tenant)
        inbox = f"/v1.0/users/{attendee}/messages"
        before = (await client.get(inbox, headers=headers)).json()["value"]
        changed = await client.patch(
            root + "/" + response.json()["id"],
            headers=headers,
            json={
                "attendees": event_body()["attendees"]
                + [{"type": "required", "emailAddress": {"address": "external@unroutable.test"}}]
            },
        )
        assert changed.status_code == 200, changed.text
        await meeting_worker(database, tenant)
        after = (await client.get(inbox, headers=headers)).json()["value"]
        assert len(after) == len(before)

"""Behavior regressions from the single phase-E review."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from mockazure.admin.errors import AdminError
from mockazure.calendar.lifecycle import export_calendars
from mockazure.identity.models import Grant
from mockazure.jobs.dispatch import dispatch_job
from mockazure.jobs.repository import claim_job
from mockazure.mail.send import reply_body
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_graph_calendar_views import recurring
from tests.integration.test_graph_calendar_workflows import event_body, meeting_worker
from tests.integration.test_graph_notifications import receiver, subscribe
from tests.integration.test_graph_send_workflows import message, setup_mail


async def consent(database, tenant, app, permissions, *, kind="application", user=None):
    async with database.sessions.begin() as session:
        row = await session.scalar(
            select(Grant).where(
                Grant.tenant_id == tenant,
                Grant.client_id == app,
                Grant.resource == "graph",
                Grant.kind == kind,
                Grant.user_id == user,
            )
        )
        if row is None:
            row = Grant(
                tenant_id=tenant,
                client_id=app,
                resource="graph",
                kind=kind,
                permissions=[],
                user_id=user,
            )
            session.add(row)
        row.permissions = sorted(set(row.permissions) | set(permissions))
        await session.flush()
        return row.id


async def worker(database, tenant, kind):
    now = datetime.now(UTC)
    async with database.sessions.begin() as session:
        claim = await claim_job(
            session, owner=uuid4(), now=now, lease_seconds=30, tenant_id=tenant, kind=kind
        )
    assert claim is not None
    return await dispatch_job(database, claim, now)


@pytest.mark.parametrize("kind", ["application", "delegated"])
async def test_actual_grant_revocation_stops_callback(database, settings, tmp_path, kind):
    with receiver(tmp_path, database) as (url, state):
        async with graph_client(database, settings) as (client, tenant, app, owner):
            await setup_mail(client, database, settings, tenant, app, owner)
            user = owner if kind == "delegated" else None
            permissions = {"Mail.ReadWrite"}
            grant = await consent(database, tenant, app, permissions, kind=kind, user=user)
            headers = await headers_for(database, settings, tenant, app, user, permissions, kind)
            await subscribe(client, headers, url, f"users/{owner}/messages")
            assert (
                await client.delete(f"/_mock/tenants/{tenant}/identity/grants/{grant}")
            ).status_code == 200
            # An already-issued public token remains distinct from saved worker authority.
            response = await client.post(
                f"/v1.0/users/{owner}/messages",
                headers=headers,
                json={"subject": "After revocation"},
            )
            assert response.status_code == 201, response.text
            result = await worker(database, tenant, "graph.notification")
            assert result.state == "failed"
            assert state["notifications"] == []


@pytest.mark.parametrize("kind", ["mail.delivery", "calendar.meeting", "upload"])
async def test_saved_workflow_authority_requires_current_grant(database, settings, kind):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        await setup_mail(client, database, settings, tenant, app, owner)
        permissions = {"Mail.Send", "Mail.ReadWrite", "Calendars.ReadWrite"}
        grant = await consent(database, tenant, app, permissions)
        headers = await headers_for(database, settings, tenant, app, None, permissions)
        root = f"/v1.0/users/{owner}"
        if kind == "mail.delivery":
            response = await client.post(
                root + "/sendMail", headers=headers, json={"message": message()}
            )
            assert response.status_code == 202, response.text
        elif kind == "calendar.meeting":
            response = await client.post(root + "/events", headers=headers, json=event_body())
            assert response.status_code == 201, response.text
        else:
            response = await client.post(
                root + "/messages", headers=headers, json={"subject": "Upload"}
            )
            assert response.status_code == 201, response.text
            response = await client.post(
                root + "/messages/" + response.json()["id"] + "/attachments/createUploadSession",
                headers=headers,
                json={
                    "AttachmentItem": {
                        "attachmentType": "file",
                        "name": "large.bin",
                        "size": 3145728,
                    }
                },
            )
            assert response.status_code == 201, response.text
            upload = response.json()["uploadUrl"]
        assert (
            await client.delete(f"/_mock/tenants/{tenant}/identity/grants/{grant}")
        ).status_code == 200
        if kind == "upload":
            assert (await client.get(upload)).status_code == 403
        else:
            assert (await worker(database, tenant, kind)).state == "failed"


@pytest.mark.parametrize("action", ["forward", "createForward"])
@pytest.mark.parametrize("content_type", ["text", "html"])
@pytest.mark.parametrize("comment", [None, "New commentary"])
def test_forward_preserves_source_body(action, content_type, comment):
    source = SimpleNamespace(
        payload={
            "subject": "Evidence",
            "body": {"contentType": content_type, "content": "Original evidence"},
        }
    )
    body = {} if comment is None else {"comment": comment}
    result = reply_body(source, SimpleNamespace(primary_smtp="owner@contoso.test"), action, body)
    assert "Original evidence" in result["body"]["content"]
    if comment:
        assert comment in result["body"]["content"]


async def test_shortened_series_retires_inapplicable_exception(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        await setup_mail(client, database, settings, tenant, app, owner)
        headers = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        root = f"/v1.0/users/{owner}"
        body = recurring()
        body["attendees"] = []
        response = await client.post(root + "/events", headers=headers, json=body)
        assert response.status_code == 201, response.text
        path = root + "/events/" + response.json()["id"]
        response = await client.get(
            path + "/instances",
            headers=headers,
            params={"startDateTime": "2026-10-24T00:00:00Z", "endDateTime": "2026-11-10T00:00:00Z"},
        )
        assert response.status_code == 200, response.text
        second = response.json()["value"][1]["id"]
        assert (
            await client.patch(
                root + "/events/" + second, headers=headers, json={"subject": "Exception"}
            )
        ).status_code == 200
        body["recurrence"]["range"]["numberOfOccurrences"] = 1
        assert (
            await client.patch(path, headers=headers, json={"recurrence": body["recurrence"]})
        ).status_code == 200
        exported = await client.get(f"/_mock/tenants/{tenant}/cloud/state")
        assert exported.status_code == 200, exported.text
        assert exported.json()["calendars"]["owners"][0]["exceptions"] == []


@pytest.mark.parametrize("attendees_only", [False, True])
async def test_alias_attendee_notifications_and_responses(database, settings, attendees_only):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        target, _, _ = await setup_mail(client, database, settings, tenant, app, owner)
        permissions = {"Calendars.ReadWrite", "Mail.Read"}
        await consent(database, tenant, app, permissions)
        headers = await headers_for(database, settings, tenant, app, None, permissions)
        provisioned = await client.put(
            f"/_mock/tenants/{tenant}/mail/mailboxes/{target}",
            json={
                "primary_smtp": "recipient@contoso.test",
                "alias": "recipient",
                "aliases": ["alias@contoso.test"],
            },
        )
        assert provisioned.status_code == 200, provisioned.text
        body = event_body()
        body["attendees"][0]["emailAddress"]["address"] = "alias@contoso.test"
        members = body["attendees"]
        if attendees_only:
            body["attendees"] = []
        root = f"/v1.0/users/{owner}/events"
        response = await client.post(root, headers=headers, json=body)
        assert response.status_code == 201, response.text
        path = root + "/" + response.json()["id"]
        if attendees_only:
            assert (
                await client.patch(path, headers=headers, json={"attendees": members})
            ).status_code == 200
        await meeting_worker(database, tenant)
        recipient = f"/v1.0/users/{target}"
        mail = (await client.get(recipient + "/messages", headers=headers)).json()["value"]
        assert len(mail) == 1
        events = (await client.get(recipient + "/events", headers=headers)).json()["value"]
        response = await client.post(
            recipient + "/events/" + events[0]["id"] + "/accept",
            headers=headers,
            json={"sendResponse": True},
        )
        assert response.status_code == 202, response.text
        await meeting_worker(database, tenant)
        result = await client.get(path, headers=headers)
        assert result.json()["attendees"][0]["status"]["response"] == "accepted"


async def test_calendar_export_container_overflow_is_explicit():
    owner = SimpleNamespace(user_id=uuid4(), default_calendar_id="c", default_group_id="g")
    groups = [SimpleNamespace(id=str(n), payload={"name": "Group"}) for n in range(1001)]
    session = SimpleNamespace(scalars=AsyncMock(side_effect=[[owner], groups, [], [], []]))
    with pytest.raises(AdminError) as error:
        await export_calendars(session, uuid4())
    assert error.value.status == 413


def test_forward_escapes_text_comment_into_original_html():
    source = SimpleNamespace(
        payload={
            "subject": "Evidence",
            "body": {"contentType": "html", "content": "<p>Original</p>"},
        }
    )
    result = reply_body(
        source,
        SimpleNamespace(primary_smtp="owner@contoso.test"),
        "forward",
        {"comment": "<untrusted comment>"},
    )
    assert result["body"]["contentType"] == "html"
    assert "&lt;untrusted comment&gt;" in result["body"]["content"]
    assert "<p>Original</p>" in result["body"]["content"]


async def test_series_edit_rejects_invalid_retained_exception_atomically(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        await setup_mail(client, database, settings, tenant, app, owner)
        headers = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        root = f"/v1.0/users/{owner}"
        body = recurring()
        body["attendees"] = []
        response = await client.post(root + "/events", headers=headers, json=body)
        assert response.status_code == 201, response.text
        path = root + "/events/" + response.json()["id"]
        instances = await client.get(
            path + "/instances",
            headers=headers,
            params={"startDateTime": "2026-10-24T00:00:00Z", "endDateTime": "2026-11-10T00:00:00Z"},
        )
        second = instances.json()["value"][1]["id"]
        response = await client.patch(
            root + "/events/" + second,
            headers=headers,
            json={"end": {"dateTime": "2026-11-01T12:00:00", "timeZone": "Eastern Standard Time"}},
        )
        assert response.status_code == 200, response.text
        before = (await client.get(path, headers=headers)).json()
        changed = await client.patch(
            path,
            headers=headers,
            json={
                "start": {"dateTime": "2026-10-25T13:00:00", "timeZone": "Eastern Standard Time"},
                "end": {"dateTime": "2026-10-25T14:00:00", "timeZone": "Eastern Standard Time"},
            },
        )
        assert changed.status_code == 400, changed.text
        after = (await client.get(path, headers=headers)).json()
        assert after["start"] == before["start"] and after["changeKey"] == before["changeKey"]
        assert (await client.get(f"/_mock/tenants/{tenant}/cloud/state")).status_code == 200

from datetime import UTC, datetime, timedelta

from tests.integration.graph_support import graph_client
from tests.integration.test_graph_calendar_workflows import event_body, meeting_worker
from tests.integration.test_graph_notifications import receiver
from tests.integration.test_graph_send_workflows import deliver, message, setup_mail
from tests.integration.workflow_support import headers_for


async def test_powershell_qualified_sendmail(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        recipient, read, send = await setup_mail(client, database, settings, tenant, app, user)
        path = f"/v1.0/users/{user}/microsoft.graph.sendMail"
        denied = await client.post(path, headers=read, json={"Message": message()})
        assert denied.status_code == 403, denied.text
        response = await client.post(
            path, headers=send, json={"Message": message(), "SaveToSentItems": True}
        )
        assert response.status_code == 202, response.text
        await deliver(database, tenant)
        found = await client.get(f"/v1.0/users/{recipient}/messages", headers=read)
        assert any(row["subject"] == "Local workflow" for row in found.json()["value"])


async def test_powershell_qualified_meeting_response(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        recipient, _, _ = await setup_mail(client, database, settings, tenant, app, user)
        headers = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
        response = await client.post(
            f"/v1.0/users/{user}/events", headers=headers, json=event_body()
        )
        assert response.status_code == 201, response.text
        event = response.json()
        await meeting_worker(database, tenant)
        found = await client.get(f"/v1.0/users/{recipient}/events", headers=headers)
        invite = next(row for row in found.json()["value"] if row["iCalUId"] == event["iCalUId"])
        response = await client.post(
            f"/v1.0/users/{recipient}/events/{invite['id']}/microsoft.graph.accept",
            headers=headers,
            json={"SendResponse": True},
        )
        assert response.status_code == 202, response.text
        await meeting_worker(database, tenant)
        found = await client.get(f"/v1.0/users/{user}/events/{event['id']}", headers=headers)
        assert found.json()["attendees"][0]["status"]["response"] == "accepted"


async def test_powershell_qualified_subscription_reauthorization(database, settings, tmp_path):
    with receiver(tmp_path, database) as (url, _):
        async with graph_client(database, settings) as (client, tenant, app, user):
            _, headers, _ = await setup_mail(client, database, settings, tenant, app, user)
            created = await client.post(
                "/v1.0/subscriptions",
                headers=headers,
                json={
                    "changeType": "created",
                    "resource": f"users/{user}/messages",
                    "notificationUrl": url,
                    "expirationDateTime": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
            )
            assert created.status_code == 201, created.text
            path = f"/v1.0/subscriptions/{created.json()['id']}/microsoft.graph.reauthorize"
            response = await client.post(path, headers=headers)
            assert response.status_code == 204, response.text

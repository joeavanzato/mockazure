from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tests.integration.graph_support import graph_client
from tests.integration.test_exchange_mail import command, exchange_headers
from tests.integration.test_mailbox_state import provision


def trace_row(now, **extra):
    return {
        "message_trace_id": str(uuid4()),
        "received": now.isoformat(),
        "sender_address": "sender@example.test",
        "recipient_address": "reader@contoso.test",
        "message_id": "<message@example.test>",
        "subject": "Invoice",
        "status": "Delivered",
        "size": 1234,
        "from_ip": "192.0.2.10",
        "to_ip": None,
        "details": [
            {
                "date": now.isoformat(),
                "event": "DELIVER",
                "action": "Delivered",
                "detail": "Delivered to Inbox",
                "data": "<root />",
            }
        ],
        **extra,
    }


async def setup_trace(client, tenant, user, app):
    await provision(client, tenant, user)
    assert (
        await client.put(
            f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/Exchange Administrator"
        )
    ).status_code == 200


async def imported(client, tenant, rows, expected=201):
    response = await client.post(
        f"/_mock/tenants/{tenant}/mail/traces", json={"source": "qualification", "records": rows}
    )
    assert response.status_code == expected, response.text
    return response


async def test_trace_window_defaults_and_limits(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await setup_trace(client, tenant, user, app)
        now = datetime.now(UTC).replace(microsecond=0)
        client._transport.app.state.clock = lambda: now
        headers = await exchange_headers(database, settings, tenant, app)
        recent = trace_row(now - timedelta(hours=1))
        old = trace_row(now - timedelta(days=3))
        await imported(client, tenant, [recent, old])
        repeated = await imported(client, tenant, [recent])
        assert repeated.json() == {"imported": 0, "unchanged": 1}
        await imported(client, tenant, [{**recent, "subject": "Modified"}], expected=409)
        result = await command(client, tenant, headers, "Get-MessageTraceV2")
        assert result.status_code == 200, result.text
        assert [r["MessageTraceId"] for r in result.json()["value"]] == [recent["message_trace_id"]]
        for params in (
            {"ResultSize": 0},
            {"ResultSize": 5001},
            {"StartDate": (now - timedelta(days=11)).isoformat()},
            {
                "StartDate": (now - timedelta(days=91)).isoformat(),
                "EndDate": (now - timedelta(days=89)).isoformat(),
            },
            {"StartDate": (now + timedelta(days=1)).isoformat()},
            {"StartingRecipientAddress": "reader@contoso.test"},
        ):
            response = await command(client, tenant, headers, "Get-MessageTraceV2", params)
            assert response.status_code == 400, response.text
        exact = await command(
            client,
            tenant,
            headers,
            "Get-MessageTraceV2",
            {"StartDate": (now - timedelta(days=10)).isoformat(), "EndDate": now.isoformat()},
        )
        assert len(exact.json()["value"]) == 2


async def test_trace_received_recipient_continuation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await setup_trace(client, tenant, user, app)
        now = datetime.now(UTC).replace(microsecond=0)
        headers = await exchange_headers(database, settings, tenant, app)
        when = now - timedelta(hours=1)
        rows = [
            trace_row(when, recipient_address=f"recipient{i:05d}@example.test", details=[])
            for i in range(5001)
        ]
        rows.append(
            trace_row(
                when - timedelta(microseconds=1),
                recipient_address=rows[-1]["recipient_address"],
                details=[],
            )
        )
        for offset in range(0, len(rows), 500):
            await imported(client, tenant, rows[offset : offset + 500])
        default = await command(client, tenant, headers, "Get-MessageTraceV2")
        assert len(default.json()["value"]) == 1000
        params = {
            "ResultSize": 5000,
            "StartDate": (now - timedelta(days=1)).isoformat(),
            "EndDate": now.isoformat(),
        }
        first = await command(client, tenant, headers, "Get-MessageTraceV2", params)
        assert first.status_code == 200, first.text
        page = first.json()["value"]
        assert len(page) == 5000
        params.update(
            EndDate=page[-1]["Received"], StartingRecipientAddress=page[-1]["RecipientAddress"]
        )
        second = await command(client, tenant, headers, "Get-MessageTraceV2", params)
        assert second.status_code == 200, second.text
        all_rows = page + second.json()["value"]
        assert len(all_rows) == 5002
        assert {r["MessageTraceId"] for r in all_rows} == {r["message_trace_id"] for r in rows}
        assert (
            await command(client, tenant, headers, "Get-MessageTraceV2", params)
        ).json() == second.json()
        # A two-field continuation cannot encode identical timestamp+recipient tuples.
        # Reject an ambiguous import instead of silently changing forensic timestamps.
        duplicate = trace_row(when, recipient_address=rows[0]["recipient_address"], details=[])
        await imported(client, tenant, [duplicate], expected=409)


async def test_trace_filters_and_scopes(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await setup_trace(client, tenant, user, app)
        headers = await exchange_headers(database, settings, tenant, app)
        now = datetime.now(UTC) - timedelta(minutes=1)
        row = trace_row(now, mailbox_user_id=str(user))
        await imported(client, tenant, [row])
        for params in (
            {"SenderAddress": ["SENDER@example.test"]},
            {"RecipientAddress": ["reader@contoso.test"]},
            {"Status": ["Delivered"]},
            {"MessageId": [row["message_id"]]},
            {"FromIP": "192.0.2.10"},
            {"MessageTraceId": row["message_trace_id"]},
            {"Subject": "Inv", "SubjectFilterType": "StartsWith"},
        ):
            response = await command(client, tenant, headers, "Get-MessageTraceV2", params)
            assert response.status_code == 200, response.text
            assert len(response.json()["value"]) == 1
        assert (
            await command(client, tenant, headers, "Get-MessageTraceV2", {"ToIP": "192.0.2.99"})
        ).json()["value"] == []
        detail_params = {
            "MessageTraceId": row["message_trace_id"],
            "RecipientAddress": row["recipient_address"],
        }
        detail = await command(client, tenant, headers, "Get-MessageTraceDetailV2", detail_params)
        assert detail.status_code == 200, detail.text
        assert [(v["Event"], v["Action"], v["Detail"]) for v in detail.json()["value"]] == [
            ("DELIVER", "Delivered", "Delivered to Inbox")
        ]
        assert (
            await command(
                client,
                tenant,
                headers,
                "Get-MessageTraceDetailV2",
                {**detail_params, "MessageTraceId": str(uuid4())},
            )
        ).json()["value"] == []
        role = f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/Exchange Administrator"
        assert (await client.delete(role)).status_code == 200
        assert (await command(client, tenant, headers, "Get-MessageTraceV2")).status_code == 403
        grant = await client.post(
            f"/_mock/tenants/{tenant}/mail/grants",
            json={
                "client_id": str(app),
                "mailbox_user_id": str(user),
                "commands": ["Get-MessageTraceV2", "Get-MessageTraceDetailV2"],
            },
        )
        assert grant.status_code == 201
        await imported(
            client,
            tenant,
            [trace_row(now - timedelta(seconds=1), recipient_address="outside@example.test")],
        )
        scoped = await command(client, tenant, headers, "Get-MessageTraceV2")
        assert len(scoped.json()["value"]) == 1
        assert scoped.json()["value"][0]["MessageTraceId"] == row["message_trace_id"]


async def test_trace_throttle(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await setup_trace(client, tenant, user, app)
        headers = await exchange_headers(database, settings, tenant, app)
        now = datetime.now(UTC)
        client._transport.app.state.clock = lambda: now
        for _ in range(100):
            response = await command(client, tenant, headers, "Get-MessageTraceV2")
            assert response.status_code == 200, response.text
        limited = await command(client, tenant, headers, "Get-MessageTraceV2")
        assert limited.status_code == 429, limited.text
        assert limited.headers["Retry-After"] == "300"
        client._transport.app.state.clock = lambda: now + timedelta(seconds=301)
        assert (await command(client, tenant, headers, "Get-MessageTraceV2")).status_code == 200

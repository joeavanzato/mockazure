from datetime import UTC, datetime, timedelta

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_graph_evidence_reads import READ, publish, record
from tests.integration.test_graph_licensing import seed_sku


async def test_persisted_continuation_fault_leaves_first_page_and_replays(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant)
        now = datetime.now(UTC) - timedelta(minutes=1)
        for _ in range(3):
            await publish(database, tenant, "signIn", record("signIn", user, now), now)
        headers = await headers_for(database, settings, tenant, app, None, READ)
        fault = await client.post(
            f"/_mock/tenants/{tenant}/graph/faults",
            json={
                "client_id": str(app),
                "resource": "signIn",
                "kind": "next_page_429",
                "retry_after": 1,
            },
        )
        assert fault.status_code == 201
        first = await client.get("/v1.0/auditLogs/signIns?$top=1", headers=headers)
        assert first.status_code == 200
        link = first.json()["@odata.nextLink"]
        denied = await client.get(link, headers=headers)
        assert denied.status_code == 429 and denied.headers["Retry-After"] == "1"
        second = await client.get(link, headers=headers)
        replay = await client.get(link, headers=headers)
        assert second.status_code == 200 and second.json() == replay.json()
        assert second.json()["value"][0]["id"] != first.json()["value"][0]["id"]

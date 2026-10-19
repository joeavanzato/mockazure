from uuid import UUID, uuid4

from mockazure.population.catalog import get_adapter
from mockazure.population.types import AdapterContext, Mutation, ResourceQuery
from tests.integration.graph_support import graph_client
from tests.integration.test_log_analytics_queries import workspace
from tests.integration.test_population_adapters import context, user


async def test_new_mailbox_can_receive_additive_folder_and_messages(database, settings):
    ctx = await context(database, settings)
    person = user(1)
    await get_adapter("directory.users").apply(ctx, [person])

    async def apply(family, operation, payload, parent=None):
        return await get_adapter(family).apply(
            ctx, [Mutation(operation=operation, logical_key=family, payload=payload, parent=parent)]
        )

    await apply(
        "mail.settings",
        "provision",
        {"id": str(ctx.tenant_id), "exchange_enabled": True, "audit_tier": "premium"},
    )
    await apply(
        "mail.mailboxes",
        "provision",
        {"id": person.payload["id"], "primary_smtp": "u1@lab.test", "alias": "u1"},
    )
    folder = {"id": "inbox", "displayName": "Inbox", "well_known_name": "inbox"}
    await apply("mail.folders", "append", folder, person.payload["id"])
    assert (await apply("mail.folders", "append", folder, person.payload["id"])).effects[
        "unchanged"
    ] == 1
    await apply(
        "mail.messages",
        "append",
        {"id": "test-message", "folder_id": "inbox", "payload": {"subject": "Profile message"}},
        person.payload["id"],
    )
    assert (await get_adapter("mail.messages").browse(ctx, ResourceQuery())).items[0].properties[
        "subject"
    ] == "Profile message"


async def test_log_population_is_typed_additive_and_publicly_queryable(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        workspace_id, _, _, headers, _ = await workspace(client, database, settings, tenant, app)
        ctx = AdapterContext(database, settings, UUID(str(tenant)))
        adapter = get_adapter("monitor.records")
        mutation = Mutation(
            operation="import",
            logical_key="log",
            parent=str(workspace_id) + "/StorageBlobLogs",
            payload={
                "id": str(uuid4()),
                "record": {
                    "TimeGenerated": "2026-10-01T12:00:00Z",
                    "OperationName": "GetBlob",
                    "AccountName": "local",
                },
            },
        )
        result = await adapter.apply(ctx, [mutation])
        assert result.effects["created"] == 1
        assert (await adapter.apply(ctx, [mutation])).effects["unchanged"] == 1
        response = await client.post(
            settings.endpoints.logs + f"/v1/workspaces/{workspace_id}/query",
            headers=headers,
            json={"query": "StorageBlobLogs | project OperationName"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["tables"][0]["rows"] == [["GetBlob"]]

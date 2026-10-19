from uuid import UUID

from mockazure.population.catalog import get_adapter
from mockazure.population.types import AdapterContext, ResourceQuery
from tests.integration.graph_support import graph_client
from tests.integration.test_population_adapters import context
from tests.integration.test_queue_workflows import message, messages, queue_call
from tests.integration.test_storage_authorization import provision

FAMILIES = [
    "storage.accounts",
    "storage.containers",
    "storage.shares",
    "storage.queues",
    "storage.blobs",
    "storage.files",
    "storage.tables",
    "storage.entities",
    "storage.messages",
    "keyvault.vaults",
    "keyvault.secrets",
    "keyvault.versions",
    "monitor.workspaces",
    "monitor.records",
    "monitor.diagnostics",
    "mail.mailboxes",
    "mail.messages",
    "mail.events",
]


async def test_every_service_browser_uses_bounded_tenant_queries(database, settings):
    ctx = await context(database, settings)
    for family in FAMILIES:
        page = await get_adapter(family).browse(ctx, ResourceQuery(limit=1))
        assert page.items == []
        assert page.continuation is None


async def test_queue_browsing_never_consumes_or_exposes_receipts(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["queue"] + "lab-items"
        assert (await queue_call(client, "PUT", url, key)).status_code == 201
        await queue_call(client, "POST", url + "/messages", key, body=message("evidence"))
        before = messages(
            await queue_call(client, "GET", url + "/messages", key, {"peekonly": "true"})
        )
        ctx = AdapterContext(database, settings, UUID(str(tenant)))
        account = await get_adapter("storage.accounts").browse(ctx, ResourceQuery())
        assert len(account.items) == 1
        assert key not in account.model_dump_json()
        adapter = get_adapter("storage.messages")
        first = await adapter.browse(ctx, ResourceQuery())
        assert first.items[0].properties["body"] == "evidence"
        detail = await adapter.browse(ctx, ResourceQuery(identifier=first.items[0].id))
        assert "receipt" not in detail.model_dump_json().lower()
        after = messages(
            await queue_call(client, "GET", url + "/messages", key, {"peekonly": "true"})
        )
        assert before == after

from sqlalchemy import select

from mockazure.identity.models import ServicePrincipal
from mockazure.providers.arm.contracts import validate_arm_payload
from tests.integration.graph_support import graph_client
from tests.integration.test_arm_authorization import assign
from tests.integration.test_arm_inventory import access
from tests.integration.test_arm_state import seed_arm

PATH = "/providers/Microsoft.ResourceGraph/resources?api-version=2024-04-01"


async def test_resourcegraph_scope_and_projection(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        allowed, base, _, resource = await seed_arm(client, tenant)
        hidden, _, _, _ = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            principal = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant,
                    ServicePrincipal.client_id == app,
                )
            )
        grant = await assign(client, base, principal, f"/subscriptions/{allowed}", "Reader")
        query = (
            "Resources | where type =~ 'Microsoft.Compute/virtualMachines' "
            "| project name, sku=tostring(properties.hardwareProfile.vmSize), subscriptionId"
        )
        response = await client.post(
            PATH, headers=headers, json={"subscriptions": [allowed, hidden], "query": query}
        )
        assert response.status_code == 200, response.text
        validate_arm_payload("QueryResponse", response.json(), namespace="resourcegraph")
        assert response.json()["data"] == [
            {"name": "vm", "sku": "Standard_D2s_v5", "subscriptionId": allowed}
        ]
        # Joining on a constant exposes a missing right-side authorization filter.
        joined = await client.post(
            PATH,
            headers=headers,
            json={
                "subscriptions": [allowed, hidden],
                "query": (
                    "Resources | extend key='all' | join kind=inner "
                    "(ResourceContainers | extend key='all') on key | summarize n=count()"
                ),
            },
        )
        assert joined.status_code == 200, joined.text
        assert joined.json()["data"] == [{"n": 2}]
        table = await client.post(
            PATH,
            headers=headers,
            json={
                "subscriptions": [allowed],
                "query": "Resources | project name, location",
                "options": {"resultFormat": "table"},
            },
        )
        assert table.status_code == 200, table.text
        assert table.json()["data"]["rows"] == [[resource["name"], resource["location"]]]
        assert table.json()["data"]["columns"] == [
            {"name": "name", "type": "string"},
            {"name": "location", "type": "string"},
        ]
        assert (await client.delete(base + "/role-assignments/" + grant["name"])).status_code == 200
        denied = await client.post(
            PATH, headers=headers, json={"subscriptions": [allowed], "query": "Resources"}
        )
        assert denied.status_code == 403, denied.text


async def test_resourcegraph_query_errors_are_explicit(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        subscription, base, _, _ = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            principal = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant,
                    ServicePrincipal.client_id == app,
                )
            )
        await assign(client, base, principal, f"/subscriptions/{subscription}", "Reader")
        for text in [
            "Resources | evaluate python('bad')",
            "Resources; DROP TABLE arm_resources",
            "SecretResources | take 1",
            "Resources | project nonexistent",
        ]:
            response = await client.post(
                PATH, headers=headers, json={"subscriptions": [subscription], "query": text}
            )
            assert response.status_code == 400, response.text
            assert response.json()["error"]["code"] == "BadRequest"
        response = await client.post(
            PATH,
            headers=headers,
            json={"subscriptions": [subscription], "query": "Resources | summarize n=count()"},
        )
        assert response.status_code == 200 and response.json()["data"] == [{"n": 1}]

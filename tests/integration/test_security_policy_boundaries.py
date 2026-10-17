from sqlalchemy import delete

from mockazure.licensing.models import SubscribedSku
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_graph_licensing import P1, PLAN1, seed_sku
from tests.integration.test_security_policies import BASE, WRITE
from tests.policy_support import policy_body


async def test_supported_policy_reader_role_and_revocation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant, P1, PLAN1)
        role = (
            f"/_mock/tenants/{tenant}/identity/users/{user}/roles/"
            "Global Secure Access Administrator"
        )
        assert (await client.put(role)).status_code == 200
        reader = await headers_for(
            database, settings, tenant, app, user, {"Policy.Read.All"}, "delegated"
        )
        assert (await client.get(BASE + "/policies", headers=reader)).status_code == 200
        writer = await headers_for(database, settings, tenant, app, user, WRITE, "delegated")
        assert (
            await client.post(BASE + "/policies", headers=writer, json=policy_body())
        ).status_code == 403
        assert (await client.delete(role)).status_code == 200
        assert (await client.get(BASE + "/policies", headers=reader)).status_code == 403


async def test_policy_cursor_rechecks_license_and_tenant_references(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant, P1, PLAN1)
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        foreign_tenant = await client.post(
            "/_mock/tenants",
            json={
                "slug": "foreign-policy",
                "display_name": "Foreign",
                "domain": "foreign-policy.test",
            },
        )
        assert foreign_tenant.status_code == 201
        other = await client.post(
            f"/_mock/tenants/{foreign_tenant.json()['id']}/identity/users",
            json={
                "upn": "user@foreign-policy.test",
                "display_name": "Other",
                "password": "local-password",
            },
        )
        assert other.status_code == 201
        body = policy_body()
        body["conditions"]["users"] = {"includeUsers": [other.json()["id"]]}
        assert (
            await client.post(BASE + "/policies", headers=headers, json=body)
        ).status_code == 400
        for state in ("disabled", "enabledForReportingButNotEnforced"):
            assert (
                await client.post(BASE + "/policies", headers=headers, json=policy_body(state))
            ).status_code == 201
        first = await client.get(BASE + "/policies?$top=1", headers=headers)
        link = first.json()["@odata.nextLink"]
        assert (
            await client.get(link.replace("/policies?", "/namedLocations?"), headers=headers)
        ).status_code == 400
        async with database.sessions.begin() as session:
            await session.execute(delete(SubscribedSku).where(SubscribedSku.tenant_id == tenant))
        assert (await client.get(link, headers=headers)).status_code == 403

from tests.integration.graph_support import graph_client
from tests.integration.test_connected_collaboration import populate
from tests.unit.test_business_relationships import values


async def test_related_resource_navigation_is_tenant_scoped(database, settings):
    async with graph_client(database, settings) as (client, tenant, *_):
        await populate(database, settings, tenant)
        drive = values("drives", 1, tenant=str(tenant))[0]
        response = await client.get(
            f"/_mock/tenants/{tenant}/resources/sharepoint.drive/related",
            params={"identifier": drive.payload["payload"]["id"], "parent": drive.parent},
        )
        assert response.status_code == 200, response.text
        rows = response.json()["items"]
        assert any(r["family"] == "sharepoint.site" and r["direction"] == "incoming" for r in rows)
        assert any(r["family"] == "sharepoint.driveItem" for r in rows)
        for row in rows:
            read = await client.get(
                f"/_mock/tenants/{tenant}/resources/{row['family']}",
                params={"identifier": row["id"], "parent": row["parent"]},
            )
            assert read.status_code == 200 and read.json()["items"]
        from uuid import uuid4

        other = await client.get(
            f"/_mock/tenants/{uuid4()}/resources/sharepoint.drive/related",
            params={"identifier": drive.payload["payload"]["id"], "parent": drive.parent},
        )
        assert other.status_code == 404

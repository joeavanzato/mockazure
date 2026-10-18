from urllib.parse import quote

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.site_drive_support import seed_site, site_grant


async def test_composite_site_and_encoded_drive_items_with_open_list_fields(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_site(client, database, tenant, user)
        auth = await headers_for(database, settings, tenant, app, None, {"Sites.Read.All"})
        site, drive, item = (quote(fixture[key], safe="") for key in ("site", "drive", "item"))
        for path in (
            f"/sites/{site}",
            "/sites/contoso.test:/sites/one",
            f"/sites/{site}/drives",
            f"/drives/{drive}/root/children",
            f"/drives/{drive}/items/{item}",
            f"/drives/{drive}/root:/evidence%20%231.txt:",
            f"/sites/{site}/lists/list!one/items/li!one/fields",
        ):
            response = await client.get("/v1.0" + path, headers=auth)
            assert response.status_code == 200, (path, response.text)
        assert response.json()["Score"] == 42
        assert (await client.get(f"/beta/sites/{site}", headers=auth)).status_code == 501


async def test_selected_requires_explicit_grant_and_cannot_search(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_site(client, database, tenant, user)
        auth = await headers_for(database, settings, tenant, app, None, {"Sites.Selected"})
        base = "/v1.0/sites/" + quote(fixture["site"], safe="")
        assert (await client.get(base, headers=auth)).status_code == 403
        await site_grant(client, tenant, fixture["site"], app, "application")
        assert (await client.get(base, headers=auth)).status_code == 200
        assert (await client.get("/v1.0/sites?search=*", headers=auth)).status_code == 403
        other = await seed_site(client, database, tenant, user, suffix="two")
        assert (
            await client.get("/v1.0/sites/" + quote(other["site"], safe=""), headers=auth)
        ).status_code == 403


async def test_delegated_search_filters_before_paging_and_revokes_pages(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        first = await seed_site(client, database, tenant, user)
        second = await seed_site(client, database, tenant, user, suffix="two")
        await seed_site(client, database, tenant, user, suffix="hidden")
        auth = await headers_for(
            database, settings, tenant, app, user, {"Sites.Read.All"}, kind="delegated"
        )
        assert (await client.get("/v1.0/sites?search=*", headers=auth)).json()["value"] == []
        grants = [await site_grant(client, tenant, f["site"], user) for f in (first, second)]
        response = await client.get("/v1.0/sites?search=*&$top=1", headers=auth)
        assert response.status_code == 200, response.text
        assert len(response.json()["value"]) == 1
        link = response.json()["@odata.nextLink"]
        for grant in grants:
            assert (
                await client.delete(f"/_mock/tenants/{tenant}/workloads/sharepoint/grants/{grant}")
            ).status_code == 204
        response = await client.get(link, headers=auth)
        assert response.status_code in {403, 410}, response.text

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


async def test_drive_aliases_share_canonical_items_and_enforce_user_access(database, settings):
    from tests.integration.graph_support import persisted_group

    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_site(client, database, tenant, user)
        async with database.sessions.begin() as session:
            group = await persisted_group(session, tenant)
            group_id = group.id
        batch = {
            "scope": fixture["site"],
            "expected_revision": 1,
            "edges": [
                {
                    "source": {"kind": kind, "id": str(identifier)},
                    "relation": "drives",
                    "target": {"kind": "drive", "id": fixture["drive"]},
                }
                for kind, identifier in (("user", user), ("group", group_id))
            ],
        }
        response = await client.post(
            f"/_mock/tenants/{tenant}/workloads/sharepoint/import", json=batch
        )
        assert response.status_code == 200, response.text
        auth = await headers_for(database, settings, tenant, app, None, {"Files.Read.All"})
        for path in (f"users/{user}/drives", f"groups/{group_id}/drives"):
            response = await client.get("/v1.0/" + path, headers=auth)
            assert response.status_code == 200, response.text
            assert response.json()["value"][0]["id"] == fixture["drive"]
        for path in (
            f"users/{user}/drive/root/children",
            f"groups/{group_id}/drive/root/children",
            "sites/" + quote(fixture["site"], safe="") + "/drive/root/children",
        ):
            response = await client.get("/v1.0/" + path, headers=auth)
            assert response.status_code == 200, response.text
            assert response.json()["value"][0]["id"] == fixture["item"]
        delegated = await headers_for(
            database, settings, tenant, app, user, {"Files.Read"}, kind="delegated"
        )
        assert (await client.get("/v1.0/me/drive/root", headers=delegated)).status_code == 403
        await site_grant(client, tenant, fixture["site"], user)
        assert (await client.get("/v1.0/me/drive/root", headers=delegated)).json()["id"] == fixture[
            "root"
        ]


async def test_site_permission_writes_are_authoritative_paged_and_native_audited(
    database, settings
):
    from sqlalchemy import select

    from mockazure.evidence.models import EvidenceSource
    from tests.integration.graph_support import registered_app

    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_site(client, database, tenant, user)
        second = await registered_app(client, tenant)
        full = await headers_for(database, settings, tenant, app, None, {"Sites.FullControl.All"})
        base = "/v1.0/sites/" + quote(fixture["site"], safe="") + "/permissions"
        ids = []
        for target in (app, second):
            response = await client.post(
                base,
                headers=full,
                json={
                    "roles": ["read"],
                    "grantedToIdentities": [
                        {"application": {"id": str(target), "displayName": "Local"}}
                    ],
                },
            )
            assert response.status_code == 201, response.text
            ids.append(response.json()["id"])
        response = await client.get(base + "?$top=1", headers=full)
        assert response.status_code == 200, response.text
        assert len(response.json()["value"]) == 1
        next_page = await client.get(response.json()["@odata.nextLink"], headers=full)
        assert next_page.status_code == 200, next_page.text
        assert {response.json()["value"][0]["id"], next_page.json()["value"][0]["id"]} == set(ids)
        assert (await client.delete(base + "/" + ids[0], headers=full)).status_code == 204
        selected = await headers_for(database, settings, tenant, app, None, {"Sites.Selected"})
        assert (await client.get(base.rsplit("/", 1)[0], headers=selected)).status_code == 403
        async with database.sessions() as session:
            facts = list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "workload"
                    )
                )
            )
        assert len(facts) == 3
        assert all(row.facts["provenance"] == "native" for row in facts)


async def test_invalid_imports_are_atomic_and_do_not_grant_permission(database, settings):
    import copy
    from uuid import uuid4

    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_site(client, database, tenant, user)
        base = f"/_mock/tenants/{tenant}/workloads/sharepoint/import"
        batch = copy.deepcopy(fixture["batch"])
        batch["expected_revision"] = 1
        batch["records"][3]["content_id"] = str(uuid4())
        assert (await client.post(base, json=batch)).status_code in {404, 422}
        batch = copy.deepcopy(fixture["batch"])
        batch["expected_revision"] = 1
        child = copy.deepcopy(batch["records"][3])
        child.update(id="ambiguous")
        child["payload"]["id"] = "ambiguous"
        batch["records"].append(child)
        batch["edges"].append(
            {
                "source": {"kind": "driveItem", "id": fixture["root"]},
                "relation": "children",
                "target": {"kind": "driveItem", "id": "ambiguous"},
            }
        )
        assert (await client.post(base, json=batch)).status_code == 422
        auth = await headers_for(
            database, settings, tenant, app, user, {"Sites.Read.All"}, kind="delegated"
        )
        # Observed permission evidence on the file has never been an authoritative site ACL.
        assert (
            await client.get("/v1.0/sites/" + quote(fixture["site"], safe=""), headers=auth)
        ).status_code == 403


async def test_site_batch_and_shared_read_budget(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_site(client, database, tenant, user)
        client._transport.app.state.settings.graph_collection_request_limit = 2
        auth = await headers_for(database, settings, tenant, app, None, {"Sites.Read.All"})
        response = await client.post(
            "/v1.0/$batch",
            headers=auth,
            json={
                "requests": [
                    {"id": str(i), "method": "GET", "url": "/sites/" + fixture["site"]}
                    for i in range(3)
                ]
            },
        )
        assert response.status_code == 200, response.text
        assert [row["status"] for row in response.json()["responses"]] == [200, 200, 429]
        assert int(response.json()["responses"][2]["headers"]["retry-after"]) > 0


async def test_delegated_site_grant_requires_operational_role_and_membership(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_site(client, database, tenant, user)
        auth = await headers_for(
            database, settings, tenant, app, user, {"Sites.FullControl.All"}, kind="delegated"
        )
        path = "/v1.0/sites/" + quote(fixture["site"], safe="") + "/permissions"
        body = {"roles": ["read"], "grantedToIdentities": [{"application": {"id": str(app)}}]}
        assert (await client.post(path, headers=auth, json=body)).status_code == 403
        role = await client.put(
            f"/_mock/tenants/{tenant}/identity/users/{user}/roles/SharePoint%20Administrator"
        )
        assert role.status_code == 200, role.text
        assert (await client.post(path, headers=auth, json=body)).status_code == 403
        await site_grant(client, tenant, fixture["site"], user)
        assert (await client.post(path, headers=auth, json=body)).status_code == 201

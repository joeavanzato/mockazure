from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.site_drive_support import seed_site, site_grant


async def test_local_download_capability_ranges_expiry_and_acl_revocation(database, settings):
    from mockazure.workloads.drives import verify_download

    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_site(client, database, tenant, user)
        grant = await site_grant(client, tenant, fixture["site"], app, "application")
        auth = await headers_for(database, settings, tenant, app, None, {"Sites.Selected"})
        path = (
            "/v1.0/drives/"
            + quote(fixture["drive"], safe="")
            + "/items/"
            + quote(fixture["item"], safe="")
            + "/content"
        )
        response = await client.get(path, headers=auth, follow_redirects=False)
        assert response.status_code == 302, response.text
        location = response.headers["location"]
        assert location.startswith("https://localhost:")
        await client.delete(f"/_mock/tenants/{tenant}/workloads/sharepoint/grants/{grant}")
        assert (await client.get(path, headers=auth, follow_redirects=False)).status_code == 403
        raw = await client.get(location, headers={"Authorization": "", "Range": "bytes=0-4"})
        assert raw.status_code == 206, raw.text
        assert raw.content == b"local"
        async with database.sessions() as session:
            assert (
                await verify_download(
                    session, location.rsplit("/", 1)[-1], datetime.now(UTC) + timedelta(minutes=6)
                )
                is None
            )


async def test_drive_delta_has_restart_safe_snapshot_and_removals(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_site(client, database, tenant, user)
        auth = await headers_for(database, settings, tenant, app, None, {"Files.Read.All"})
        base = "/v1.0/drives/" + quote(fixture["drive"], safe="") + "/root/delta"
        response = await client.get(base + "?$top=1", headers=auth)
        assert response.status_code == 200, response.text
        ids = [row["id"] for row in response.json()["value"]]
        while link := response.json().get("@odata.nextLink"):
            response = await client.get(link, headers=auth)
            assert response.status_code == 200, response.text
            ids.extend(row["id"] for row in response.json()["value"])
        assert set(ids) == {fixture["root"], fixture["item"]}
        checkpoint = response.json()["@odata.deltaLink"]
        # Deleting a file also tombstones its metadata children in one validated transaction.
        batch = {
            "scope": fixture["site"],
            "expected_revision": 1,
            "tombstones": [
                {"kind": kind, "id": identifier}
                for kind, identifier in (
                    ("driveItem", fixture["item"]),
                    ("driveItemVersion", "version!one"),
                    ("permission", "evidence-permission!one"),
                )
            ],
        }
        response = await client.post(
            f"/_mock/tenants/{tenant}/workloads/sharepoint/import", json=batch
        )
        assert response.status_code == 200, response.text
        await database.engine.dispose()
        changes = []
        response = await client.get(checkpoint, headers=auth)
        while True:
            assert response.status_code == 200, response.text
            changes.extend(response.json()["value"])
            if not (link := response.json().get("@odata.nextLink")):
                break
            response = await client.get(link, headers=auth)
        assert any(row["id"] == fixture["item"] and "deleted" in row for row in changes)

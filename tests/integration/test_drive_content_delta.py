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


async def test_export_reset_restore_invalidates_old_cursor_and_preserves_bytes(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_site(client, database, tenant, user)
        admin = f"/_mock/tenants/{tenant}/workloads/sharepoint"
        auth = await headers_for(database, settings, tenant, app, None, {"Files.Read.All"})
        path = "/v1.0/drives/" + quote(fixture["drive"], safe="")
        old = await client.get(path + "/root/delta?token=latest", headers=auth)
        assert old.status_code == 200, old.text
        checkpoint = old.json()["@odata.deltaLink"]
        exported = await client.get(admin + "/export", params={"scope": fixture["site"]})
        assert exported.status_code == 200, exported.text
        reset = await client.post(
            admin + "/reset", json={"scope": fixture["site"], "expected_revision": 1}
        )
        assert reset.status_code == 200, reset.text
        batch = exported.json()
        batch["expected_revision"] = 0
        restored = await client.post(admin + "/import", json=batch)
        assert restored.status_code == 200, restored.text
        assert (await client.get(checkpoint, headers=auth)).status_code == 410
        content = await client.get(
            path + "/items/" + quote(fixture["item"], safe="") + "/content", headers=auth
        )
        assert content.status_code == 302, content.text
        assert (
            await client.get(content.headers["location"])
        ).content == b"local drive evidence\x00bytes"


async def test_cleanup_retains_cursor_versions_and_expires_capability_pins(database, settings):
    from sqlalchemy import select, update

    from mockazure.content.models import ContentReference
    from mockazure.workloads.cleanup import cleanup_workloads
    from mockazure.workloads.models import WorkloadCursor, WorkloadDownload, WorkloadVersion

    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_site(client, database, tenant, user)
        auth = await headers_for(database, settings, tenant, app, None, {"Files.Read.All"})
        path = "/v1.0/drives/" + quote(fixture["drive"], safe="")
        first = await client.get(path + "/root/delta?$top=1", headers=auth)
        assert first.status_code == 200, first.text
        await client.get(
            path + "/items/" + quote(fixture["item"], safe="") + "/content", headers=auth
        )
        batch = fixture["batch"]
        batch["expected_revision"] = 1
        batch["records"][2]["payload"]["name"] = "Renamed root"
        assert (
            await client.post(f"/_mock/tenants/{tenant}/workloads/sharepoint/import", json=batch)
        ).status_code == 200
        async with database.sessions.begin() as session:
            await cleanup_workloads(session, tenant, datetime.now(UTC), 1000)
            old = list(
                await session.scalars(
                    select(WorkloadVersion).where(
                        WorkloadVersion.tenant_id == tenant, WorkloadVersion.valid_to.is_not(None)
                    )
                )
            )
            assert old
        resumed = await client.get(first.json()["@odata.nextLink"], headers=auth)
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["value"][0]["name"] == "root"
        latest = await client.get(path + "/root/delta?token=latest", headers=auth)
        assert latest.status_code == 200, latest.text
        import hashlib
        from urllib.parse import parse_qs, urlsplit

        token = parse_qs(urlsplit(latest.json()["@odata.deltaLink"]).query)["$deltatoken"][0]
        retained = hashlib.sha256(token.encode()).hexdigest()
        async with database.sessions.begin() as session:
            past = datetime.now(UTC) - timedelta(days=2)
            for model in (WorkloadCursor, WorkloadDownload):
                await session.execute(
                    update(model)
                    .where(model.tenant_id == tenant, model.token_hash != retained)
                    .values(expires_at=past)
                )
            await cleanup_workloads(session, tenant, datetime.now(UTC), 1000)
            assert not list(
                await session.scalars(
                    select(WorkloadVersion).where(
                        WorkloadVersion.tenant_id == tenant, WorkloadVersion.valid_to.is_not(None)
                    )
                )
            )
            assert not list(
                await session.scalars(
                    select(ContentReference).where(
                        ContentReference.tenant_id == tenant,
                        ContentReference.owner_kind == "workload-download",
                    )
                )
            )

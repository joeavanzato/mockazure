import copy
import hashlib
import io
import json
import zipfile
from urllib.parse import quote
from uuid import uuid4

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.site_drive_support import seed_site
from tests.integration.test_content_storage import upload


async def test_public_child_ids_are_scoped_to_parent_and_site(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        auth = await headers_for(database, settings, tenant, app, None, {"Sites.Read.All"})
        for suffix in ("first", "second"):
            f = await seed_site(client, database, tenant, user, suffix=suffix)
            second_blob = await upload(database, tenant, (suffix + " other version").encode())
            batch = copy.deepcopy(f["batch"])
            batch["expected_revision"] = 1
            version = next(r for r in batch["records"] if r["kind"] == "driveItemVersion")
            old_version = version["id"]
            version.update(id="1.0", key="first-version")
            version["payload"]["id"] = "1.0"
            li = next(r for r in batch["records"] if r["kind"] == "listItem")
            old_li = li["id"]
            li.update(id="1", key="first-list-item")
            li["payload"]["id"] = "1"
            for edge in batch["edges"]:
                for side in ("source", "target"):
                    if edge[side]["id"] == old_version:
                        edge[side].update(id="1.0", key="first-version")
                    if edge[side]["id"] == old_li:
                        edge[side].update(id="1", key="first-list-item")
            other_file = copy.deepcopy(batch["records"][3])
            other_file.update(id="other-file", key="other-file")
            other_file["payload"].update(id="other-file", name="Other.bin", size=second_blob.size)
            other_file["content_id"] = str(second_blob.id)
            other_version = copy.deepcopy(version)
            other_version.update(key="other-version", content_id=str(second_blob.id))
            other_version["payload"]["size"] = second_blob.size
            other_list = copy.deepcopy(next(r for r in batch["records"] if r["kind"] == "list"))
            other_list.update(id="other-list")
            other_list["payload"]["id"] = "other-list"
            other_li = copy.deepcopy(li)
            other_li["key"] = "other-list-item"
            batch["records"] += [other_file, other_version, other_list, other_li]

            def edge(sk, sid, relation, tk, tid, key=None):
                return {
                    "source": {"kind": sk, "id": sid},
                    "relation": relation,
                    "target": {"kind": tk, "id": tid, **({"key": key} if key else {})},
                }

            batch["edges"] += [
                edge("driveItem", f["root"], "children", "driveItem", "other-file"),
                edge(
                    "driveItem",
                    "other-file",
                    "versions",
                    "driveItemVersion",
                    "1.0",
                    "other-version",
                ),
                edge("site", f["site"], "lists", "list", "other-list"),
                edge("list", "other-list", "items", "listItem", "1", "other-list-item"),
            ]
            batch["tombstones"] = [
                {"kind": "driveItemVersion", "id": old_version},
                {"kind": "listItem", "id": old_li},
            ]
            response = await client.post(
                f"/_mock/tenants/{tenant}/workloads/sharepoint/import", json=batch
            )
            assert response.status_code == 200, response.text
            for item_id, expected in (
                (f["item"], b"local drive evidence\x00bytes"),
                ("other-file", (suffix + " other version").encode()),
            ):
                base = (
                    "/v1.0/drives/"
                    + quote(f["drive"], safe="")
                    + "/items/"
                    + quote(item_id, safe="")
                    + "/versions"
                )
                versions = await client.get(base, headers=auth)
                assert [v["id"] for v in versions.json()["value"]] == ["1.0"]
                assert (await client.get(base + "/1.0", headers=auth)).json()["id"] == "1.0"
                redirect = await client.get(base + "/1.0/content", headers=auth)
                assert (await client.get(redirect.headers["location"])).content == expected
            for list_id in ("list!" + suffix, "other-list"):
                path = (
                    "/v1.0/sites/"
                    + quote(f["site"], safe="")
                    + "/lists/"
                    + quote(list_id, safe="")
                    + "/items/1"
                )
                assert (await client.get(path, headers=auth)).json()["id"] == "1"
            exported = await client.get(
                f"/_mock/tenants/{tenant}/workloads/sharepoint/export", params={"scope": f["site"]}
            )
            assert any(
                r.get("key") == "other-version" and r["id"] == "1.0"
                for r in exported.json()["records"]
            )
            exported_batch = exported.json()
            reset = await client.post(
                f"/_mock/tenants/{tenant}/workloads/sharepoint/reset",
                json={"scope": f["site"], "expected_revision": exported_batch["expected_revision"]},
            )
            assert reset.status_code == 200, reset.text
            exported_batch["expected_revision"] = 0
            restored = await client.post(
                f"/_mock/tenants/{tenant}/workloads/sharepoint/import", json=exported_batch
            )
            assert restored.status_code == 200, restored.text
            item = await client.get(
                "/v1.0/drives/" + quote(f["drive"], safe="") + "/items/other-file/versions/1.0",
                headers=auth,
            )
            assert item.status_code == 200 and item.json()["id"] == "1.0"


async def test_site_drives_least_permission_pages(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        f = await seed_site(client, database, tenant, user)
        batch = copy.deepcopy(f["batch"])
        batch["expected_revision"] = 1
        drive = copy.deepcopy(batch["records"][1])
        drive["id"] = drive["payload"]["id"] = "second-drive"
        batch["records"].append(drive)
        batch["edges"].append(
            {
                "source": {"kind": "site", "id": f["site"]},
                "relation": "drives",
                "target": {"kind": "drive", "id": "second-drive"},
            }
        )
        assert (
            await client.post(f"/_mock/tenants/{tenant}/workloads/sharepoint/import", json=batch)
        ).status_code == 200
        auth = await headers_for(database, settings, tenant, app, None, {"Files.Read.All"})
        response = await client.get(
            "/v1.0/sites/" + quote(f["site"], safe="") + "/drives?$top=1", headers=auth
        )
        assert response.status_code == 200, response.text
        next_page = await client.get(response.json()["@odata.nextLink"], headers=auth)
        assert next_page.status_code == 200, next_page.text
        assert {response.json()["value"][0]["id"], next_page.json()["value"][0]["id"]} == {
            f["drive"],
            "second-drive",
        }


async def test_site_authority_uses_navigation_not_substring(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        f = await seed_site(client, database, tenant, user, suffix="permissions-team")
        auth = await headers_for(database, settings, tenant, app, None, {"Files.Read.All"})
        tail = "/items/" + quote(f["item"], safe="") + "/permissions"
        for prefix in (
            "/v1.0/drives/" + quote(f["drive"], safe=""),
            "/v1.0/sites/" + quote(f["site"], safe="") + "/drive",
        ):
            response = await client.get(prefix + tail, headers=auth)
            assert response.status_code == 200, response.text
        site_auth = await headers_for(database, settings, tenant, app, None, {"Sites.Read.All"})
        response = await client.get(
            "/v1.0/sites/contoso.test:/sites/permissions-team", headers=site_auth
        )
        assert response.status_code == 200, response.text


async def test_portable_archive_restores_after_original_bytes_are_removed(database, settings):
    from mockazure.content.repository import reset_content

    async with graph_client(database, settings) as (client, tenant, app, user):
        f = await seed_site(client, database, tenant, user)
        base = f"/_mock/tenants/{tenant}/workloads/sharepoint"
        response = await client.get(base + "/export", params={"scope": f["site"], "format": "zip"})
        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            rows = [json.loads(line) for line in archive.read("manifest.ndjson").splitlines()]
            header = next(r["value"] for r in rows if r["type"] == "scope")
            contents = [r["value"] for r in rows if r["type"] == "content"]
            assert header["manifest"]["tenant_id"] == str(tenant)
            assert contents and contents[0]["sha256"] == f["blob"].sha256
            await client.post(base + "/reset", json={"scope": f["site"], "expected_revision": 1})
            async with database.sessions.begin() as session:
                await reset_content(session, tenant)
            for descriptor in contents:
                raw = archive.read("content/" + descriptor["id"] + ".bin")
                assert hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
                restored = await upload(database, tenant, raw)
                assert str(restored.id) != descriptor["id"]
            batch = {
                **header,
                "expected_revision": 0,
                "records": [r["value"] for r in rows if r["type"] == "record"],
                "edges": [r["value"] for r in rows if r["type"] == "edge"],
            }
            batch["manifest"]["content"] = contents
        wrong = copy.deepcopy(batch)
        wrong["manifest"]["tenant_id"] = str(uuid4())
        assert (await client.post(base + "/import", json=wrong)).status_code == 422
        wrong = copy.deepcopy(batch)
        wrong["manifest"]["content"][0]["sha256"] = "0" * 64
        assert (await client.post(base + "/import", json=wrong)).status_code == 422
        result = await client.post(base + "/import", json=batch)
        assert result.status_code == 200, result.text
        auth = await headers_for(database, settings, tenant, app, None, {"Files.Read.All"})
        path = (
            "/v1.0/drives/"
            + quote(f["drive"], safe="")
            + "/items/"
            + quote(f["item"], safe="")
            + "/content"
        )
        redirect = await client.get(path, headers=auth)
        assert (
            await client.get(redirect.headers["location"])
        ).content == b"local drive evidence\x00bytes"

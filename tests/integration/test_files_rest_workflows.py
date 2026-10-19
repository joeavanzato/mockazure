"""FileREST bytes, share snapshots, paths and real lease conditions."""

from xml.etree import ElementTree as ET

from tests.integration.graph_support import graph_client
from tests.integration.test_blob_workflows import call
from tests.integration.test_storage_authorization import provision


async def setup_files(client, database, settings, tenant, app):
    _, _, endpoints, key = await provision(client, database, settings, tenant, app)
    url = endpoints["file"] + "evidence"
    result = await call(client, "PUT", url, key, {"restype": "share"})
    assert result.status_code == 201, result.text
    return url, key


async def create_file(client, url, key, size, extra=None):
    return await call(
        client,
        "PUT",
        url,
        key,
        extra={"x-ms-type": "file", "x-ms-content-length": str(size)} | (extra or {}),
    )


async def put_range(client, url, key, start, data, *, clear=None, extra=None):
    length = clear if clear is not None else len(data)
    return await call(
        client,
        "PUT",
        url,
        key,
        {"comp": "range"},
        data,
        {
            "x-ms-range": f"bytes={start}-{start + length - 1}",
            "x-ms-write": "clear" if clear is not None else "update",
        }
        | (extra or {}),
    )


async def test_file_cross_chunk_ranges(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_files(client, database, settings, tenant, app)
        for path in ("one", "one/two"):
            result = await call(client, "PUT", url + "/" + path, key, {"restype": "directory"})
            assert result.status_code == 201, result.text
        target = url + "/one/two/bytes.bin"
        size = 1048576 + 30
        result = await create_file(client, target, key, size)
        assert result.status_code == 201, result.text
        payload = b"boundary-range-data"
        expected = bytearray(size)
        expected[1048570 : 1048570 + len(payload)] = payload
        result = await put_range(client, target, key, 1048570, payload)
        assert result.status_code == 201, result.text
        assert (await call(client, "GET", target, key)).content == bytes(expected)
        result = await put_range(client, target, key, 1048574, b"", clear=7)
        assert result.status_code == 201, result.text
        expected[1048574:1048581] = bytes(7)
        ranged = await call(
            client, "GET", target, key, extra={"x-ms-range": "bytes=1048568-1048590"}
        )
        assert ranged.status_code == 206 and ranged.content == bytes(expected[1048568:1048591])
        listed = await call(client, "GET", target, key, {"comp": "rangelist"})
        assert listed.status_code == 200, listed.text
        ranges = [
            (int(n.findtext("Start")), int(n.findtext("End")))
            for n in ET.fromstring(listed.content).findall("Range")
        ]
        assert ranges == [(1048570, 1048588)]
        resized = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "properties"},
            extra={"x-ms-content-length": "1048577"},
        )
        assert resized.status_code == 200, resized.text
        assert (await call(client, "GET", target, key)).content == bytes(expected[:1048577])
        assert (await put_range(client, target, key, 1048576, b"too long")).status_code == 416
        assert (await call(client, "GET", target, key)).content == bytes(expected[:1048577])


async def test_file_snapshot_and_conditions(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_files(client, database, settings, tenant, app)
        target = url + "/original.bin"
        assert (await create_file(client, target, key, 8)).status_code == 201
        assert (await put_range(client, target, key, 0, b"original")).status_code == 201
        snapshot = await call(client, "PUT", url, key, {"restype": "share", "comp": "snapshot"})
        assert snapshot.status_code == 201, snapshot.text
        stamp = snapshot.headers["x-ms-snapshot"]
        lease = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "lease"},
            extra={"x-ms-lease-action": "acquire", "x-ms-lease-duration": "-1"},
        )
        assert lease.status_code == 201, lease.text
        identity = lease.headers["x-ms-lease-id"]
        assert (await put_range(client, target, key, 0, b"no lease")).status_code == 412
        assert (
            await put_range(client, target, key, 0, b"modified", extra={"x-ms-lease-id": identity})
        ).status_code == 201
        released = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "lease"},
            extra={"x-ms-lease-action": "release", "x-ms-lease-id": identity},
        )
        assert released.status_code == 200
        assert (
            await put_range(client, target, key, 0, b"stale-id", extra={"x-ms-lease-id": identity})
        ).status_code == 412
        saved = await call(client, "GET", target, key, {"sharesnapshot": stamp})
        assert saved.content == b"original"
        assert (await call(client, "GET", target, key)).content == b"modified"
        assert (
            await call(client, "DELETE", target, key, {"sharesnapshot": stamp})
        ).status_code == 400
        assert (await call(client, "DELETE", url, key, {"restype": "share"})).status_code == 409
        assert (
            await call(client, "DELETE", url, key, {"restype": "share", "sharesnapshot": stamp})
        ).status_code == 202
        assert (await call(client, "GET", target, key, {"sharesnapshot": stamp})).status_code == 404


async def test_file_paths_and_metadata(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_files(client, database, settings, tenant, app)
        parent = url + "/Résumé"
        result = await call(
            client, "PUT", parent, key, {"restype": "directory"}, extra={"x-ms-meta-owner": "team"}
        )
        assert result.status_code == 201, result.text
        assert (await create_file(client, parent + "/Report%20One.bin", key, 4)).status_code == 201
        assert (
            await put_range(client, url + "/résumé/report%20one.bin", key, 0, b"data")
        ).status_code == 201
        assert (await call(client, "GET", parent + "/REPORT%20ONE.BIN", key)).content == b"data"
        assert (
            await call(client, "DELETE", parent, key, {"restype": "directory"})
        ).status_code == 409
        assert (await create_file(client, url + "/CON", key, 0)).status_code == 400
        assert (await create_file(client, parent + "/illegal%5Cname", key, 0)).status_code == 400
        assert (await create_file(client, parent + "/Report%20One.bin/", key, 0)).status_code == 400
        assert (await create_file(client, url + "/absent/child", key, 0)).status_code == 404
        assert (await create_file(client, parent + "/trailing...", key, 0)).status_code == 201
        assert (await call(client, "HEAD", parent + "/trailing", key)).status_code == 200
        assert (
            await create_file(
                client, parent + "/kept...", key, 0, {"x-ms-allow-trailing-dot": "true"}
            )
        ).status_code == 201
        assert (await call(client, "HEAD", parent + "/kept", key)).status_code == 404
        page = await call(
            client, "GET", parent, key, {"restype": "directory", "comp": "list", "maxresults": "1"}
        )
        assert page.status_code == 200, page.text
        names = []
        for _ in range(5):
            xml = ET.fromstring(page.content)
            names.extend(n.findtext("Name") for n in xml.findall("Entries/File"))
            marker = xml.findtext("NextMarker")
            if not marker:
                break
            page = await call(
                client,
                "GET",
                parent,
                key,
                {"restype": "directory", "comp": "list", "maxresults": "1", "marker": marker},
            )
            assert page.status_code == 200
        assert len(names) == 3 and len(set(names)) == 3
        props = await call(client, "GET", parent, key, {"restype": "directory"})
        assert props.headers["x-ms-meta-owner"] == "team"
        assert (await call(client, "DELETE", parent + "/Report%20One.bin", key)).status_code == 202


async def test_file_state_restore_preserves_content_and_revokes_lease(database, settings):
    import copy

    from tests.integration.test_storage_authorization import ARGS

    async with graph_client(database, settings) as (client, tenant, app, user):
        path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["file"] + "evidence"
        assert (await call(client, "PUT", url, key, {"restype": "share"})).status_code == 201
        target = url + "/kept"
        assert (await create_file(client, target, key, 4)).status_code == 201
        assert (await put_range(client, target, key, 0, b"kept")).status_code == 201
        snapshot = await call(client, "PUT", url, key, {"restype": "share", "comp": "snapshot"})
        stamp = snapshot.headers["x-ms-snapshot"]
        lease = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "lease"},
            extra={"x-ms-lease-action": "acquire", "x-ms-lease-duration": "-1"},
        )
        lease_id = lease.headers["x-ms-lease-id"]
        state_url = f"/_mock/tenants/{tenant}/cloud/storageState"
        state = (await client.get(state_url)).json()
        assert len(state["accounts"][0]["files"]) == 2
        assert lease_id not in str(state) and key not in str(state)
        invalid = copy.deepcopy(state)
        next(n for n in invalid["accounts"][0]["files"][0]["nodes"] if not n["directory"])[
            "content"
        ]["sha256"] = "0" * 64
        bad = await client.put(state_url, json=invalid)
        assert bad.status_code == 422, bad.text
        assert (await call(client, "GET", target, key)).content == b"kept"
        restored = await client.put(state_url, json=state)
        assert restored.status_code == 200, restored.text
        assert (await call(client, "GET", target, key)).status_code == 403
        new_key = (await client.post(path + "/listKeys", params=ARGS, headers=arm)).json()["keys"][
            0
        ]["value"]
        assert (await call(client, "GET", target, new_key)).content == b"kept"
        assert (
            await call(client, "GET", target, new_key, {"sharesnapshot": stamp})
        ).content == b"kept"
        assert (
            await put_range(client, target, new_key, 0, b"fail", extra={"x-ms-lease-id": lease_id})
        ).status_code == 412
        assert (await put_range(client, target, new_key, 0, b"pass")).status_code == 201
        assert (await client.put(state_url, json={"accounts": []})).status_code == 200
        from sqlalchemy import func, select

        from mockazure.content.models import ContentReference
        from mockazure.storage.file_models import FileNode

        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(FileNode).where(FileNode.tenant_id == tenant)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ContentReference)
                    .where(
                        ContentReference.tenant_id == tenant,
                        ContentReference.owner_kind == "file-node",
                    )
                )
                == 0
            )


async def test_file_share_snapshot_listing_and_paging(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_files(client, database, settings, tenant, app)
        for _ in range(2):
            assert (
                await call(client, "PUT", url, key, {"restype": "share", "comp": "snapshot"})
            ).status_code == 201
        root = url.rsplit("/", 1)[0] + "/"
        marker = ""
        items = []
        for _ in range(5):
            query = {"comp": "list", "include": "snapshots,metadata", "maxresults": "1"}
            if marker:
                query["marker"] = marker
            response = await call(client, "GET", root, key, query)
            assert response.status_code == 200, response.text
            xml = ET.fromstring(response.content)
            items.extend(
                (n.findtext("Name"), n.findtext("Snapshot")) for n in xml.findall("Shares/Share")
            )
            marker = xml.findtext("NextMarker")
            if not marker:
                break
        assert len(items) == 3 and len(set(items)) == 3
        assert sum(bool(s) for _, s in items) == 2


async def test_files_reject_ignored_options_and_atomic_bad_ranges(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_files(client, database, settings, tenant, app)
        target = url + "/atomic"
        assert (await create_file(client, target, key, 4)).status_code == 201
        assert (await put_range(client, target, key, 0, b"kept")).status_code == 201
        for query, extra, body in [
            ({"comp": "range"}, {"x-ms-write": "update", "x-ms-range": "bytes=0-3"}, b"short"),
            ({"comp": "range"}, {"x-ms-write": "clear", "x-ms-range": "bytes=0-2"}, b"bad"),
            (
                {"comp": "range"},
                {"x-ms-write": "update", "x-ms-range": "bytes=0-3", "Content-MD5": "invalid"},
                b"bad!",
            ),
            ({"comp": "metadata"}, {"If-Match": '"not-a-file-condition"'}, b""),
            ({"comp": "metadata"}, {"x-ms-file-attributes": "ReadOnly"}, b""),
            ({"comp": "properties"}, {"x-ms-file-permission": "D:invented"}, b""),
            ({"comp": "rename"}, {}, b""),
        ]:
            result = await call(client, "PUT", target, key, query, body, extra)
            assert result.status_code in {400, 501}, result.text
            assert (await call(client, "GET", target, key)).content == b"kept"
        assert (
            await call(client, "GET", target, key, {"unimplemented": "true"})
        ).status_code == 400


async def test_file_oauth_privileged_rights_and_backup_intent(database, settings):
    from sqlalchemy import delete, select

    from mockazure.identity.models import ServicePrincipal
    from mockazure.providers.arm.models import RoleAssignment
    from tests.integration.graph_support import headers_for
    from tests.integration.test_arm_authorization import assign

    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_files(client, database, settings, tenant, app)
        target = url + "/private"
        assert (await create_file(client, target, key, 4)).status_code == 201
        assert (await put_range(client, target, key, 0, b"data")).status_code == 201
        token = await headers_for(
            database, settings, tenant, app, None, [], audience="https://storage.azure.com/"
        )
        token["x-ms-version"] = "2025-11-05"
        assert (await client.get(target, headers=token)).status_code == 403
        state = (await client.get(f"/_mock/tenants/{tenant}/cloud/storageState")).json()
        account = state["accounts"][0]["id"]
        async with database.sessions() as session:
            principal = await session.scalar(
                select(ServicePrincipal.id).where(ServicePrincipal.client_id == app)
            )
        await assign(
            client,
            f"/_mock/tenants/{tenant}/subscriptions/{account.split('/')[2]}",
            principal,
            account,
            "Storage File Data Privileged Reader",
        )
        assert (await client.get(target, headers=token)).status_code == 403
        token["x-ms-file-request-intent"] = "backup"
        response = await client.get(target, headers=token)
        assert response.status_code == 200 and response.content == b"data", response.text
        async with database.sessions.begin() as session:
            await session.execute(
                delete(RoleAssignment).where(
                    RoleAssignment.tenant_id == tenant, RoleAssignment.client_id == app
                )
            )
        assert (await client.get(target, headers=token)).status_code == 403


async def test_file_migration_preserves_blob_and_refuses_live_file_loss(database, settings):
    import pytest
    from alembic import command
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError

    from mockazure.db.migrations import configuration

    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_files(client, database, settings, tenant, app)
        engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
        try:
            with engine.begin() as connection:
                config = configuration()
                config.attributes["connection"] = connection
                command.downgrade(config, "062")
                command.upgrade(config, "head")
                assert connection.scalar(text("SELECT count(*) FROM storage_namespaces")) == 1
            assert (await create_file(client, url + "/preserved", key, 4)).status_code == 201
            with pytest.raises(DBAPIError, match="Export and reset File content"):
                with engine.begin() as connection:
                    config = configuration()
                    config.attributes["connection"] = connection
                    command.downgrade(config, "062")
            assert (await call(client, "GET", url + "/preserved", key)).content == bytes(4)
        finally:
            engine.dispose()


async def test_file_unaligned_clear_allocation_and_property_replacement(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_files(client, database, settings, tenant, app)
        target = url + "/allocation"
        assert (
            await create_file(
                client,
                target,
                key,
                4096,
                {"x-ms-cache-control": "private", "x-ms-content-language": "en"},
            )
        ).status_code == 201
        assert (await put_range(client, target, key, 0, b"x" * 4096)).status_code == 201
        assert (await put_range(client, target, key, 768, b"", clear=1537)).status_code == 201
        result = await call(client, "GET", target, key, {"comp": "rangelist"})
        pairs = [
            (int(n.findtext("Start")), int(n.findtext("End")))
            for n in ET.fromstring(result.content).findall("Range")
        ]
        assert pairs == [(0, 1023), (2048, 4095)]
        content = (await call(client, "GET", target, key)).content
        assert content == b"x" * 768 + bytes(1537) + b"x" * (4096 - 2305)
        assert (
            await call(
                client,
                "PUT",
                target,
                key,
                {"comp": "properties"},
                extra={"x-ms-content-type": "application/custom"},
            )
        ).status_code == 200
        result = await call(client, "HEAD", target, key)
        assert result.headers["Content-Type"] == "application/custom"
        assert "Cache-Control" not in result.headers and "Content-Language" not in result.headers


async def test_file_listing_contract_encoding_and_readonly_overwrite(database, settings):
    from urllib.parse import quote

    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_files(client, database, settings, tenant, app)
        directory = url + "/" + quote("odd\ufffe")
        assert (
            await call(client, "PUT", directory, key, {"restype": "directory"})
        ).status_code == 201
        name = "file\uffff"
        target = directory + "/" + quote(name)
        assert (await create_file(client, target, key, 4)).status_code == 201
        listing = await call(
            client,
            "GET",
            directory,
            key,
            {
                "restype": "directory",
                "comp": "list",
                "include": "Timestamps,ETag",
                "prefix": "file",
            },
        )
        xml = ET.fromstring(listing.content)
        assert xml.attrib["Encoded"] == "true" and xml.attrib["DirectoryPath"] == quote(
            "odd\ufffe", safe=""
        )
        assert xml.findtext("DirectoryId")
        entry = xml.find("Entries/File")
        assert entry.find("Name").attrib["Encoded"] == "true"
        assert entry.findtext("Name") == quote(name, safe="")
        assert entry.findtext("Properties/Last-Modified")
        assert (
            await call(
                client,
                "GET",
                directory,
                key,
                {"restype": "directory", "comp": "list"},
                extra={"x-ms-file-extended-info": "invalid"},
            )
        ).status_code == 400
        assert (
            await call(
                client,
                "PUT",
                target,
                key,
                {"comp": "properties"},
                extra={"x-ms-file-attributes": "ReadOnly"},
            )
        ).status_code == 200
        assert (await create_file(client, target, key, 0)).status_code == 409
        assert (await call(client, "HEAD", target, key)).headers["content-length"] == "4"


async def test_file_metadata_properties_and_lease_change_break(database, settings):
    from uuid import uuid4

    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_files(client, database, settings, tenant, app)
        directory = url + "/metadata"
        assert (
            await call(client, "PUT", directory, key, {"restype": "directory"})
        ).status_code == 201
        assert (
            await call(
                client,
                "PUT",
                directory,
                key,
                {"restype": "directory", "comp": "metadata"},
                extra={"x-ms-meta-label": "directory"},
            )
        ).status_code == 200
        assert (
            await call(
                client,
                "PUT",
                directory,
                key,
                {"restype": "directory", "comp": "properties"},
                extra={"x-ms-file-attributes": "Directory|Hidden"},
            )
        ).status_code == 200
        target = directory + "/file"
        assert (await create_file(client, target, key, 4)).status_code == 201
        assert (
            await call(
                client, "PUT", target, key, {"comp": "metadata"}, extra={"x-ms-meta-label": "file"}
            )
        ).status_code == 200
        for path, query, label in (
            (directory, {"restype": "directory"}, "directory"),
            (target, {}, "file"),
        ):
            result = await call(client, "HEAD", path, key, query)
            assert result.headers["x-ms-meta-label"] == label
        lease = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "lease"},
            extra={"x-ms-lease-action": "acquire", "x-ms-lease-duration": "-1"},
        )
        old = lease.headers["x-ms-lease-id"]
        new = str(uuid4())
        changed = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "lease"},
            extra={
                "x-ms-lease-action": "change",
                "x-ms-lease-id": old,
                "x-ms-proposed-lease-id": new,
            },
        )
        assert changed.status_code == 200 and changed.headers["x-ms-lease-id"] == new
        assert (
            await put_range(client, target, key, 0, b"fail", extra={"x-ms-lease-id": old})
        ).status_code == 412
        assert (
            await put_range(client, target, key, 0, b"pass", extra={"x-ms-lease-id": new})
        ).status_code == 201
        assert (
            await call(
                client, "PUT", target, key, {"comp": "lease"}, extra={"x-ms-lease-action": "break"}
            )
        ).status_code == 202
        assert (await put_range(client, target, key, 0, b"free")).status_code == 201
        assert (await call(client, "DELETE", target, key)).status_code == 202
        assert (
            await call(client, "DELETE", directory, key, {"restype": "directory"})
        ).status_code == 202


async def test_file_account_and_service_sas_access(database, settings):
    from datetime import UTC, datetime, timedelta
    from urllib.parse import parse_qsl

    from azure.storage.fileshare import FileSasPermissions, generate_file_sas

    from tests.integration.test_storage_authorization import account_sas

    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_files(client, database, settings, tenant, app)
        client.headers.pop("Authorization", None)
        target = url + "/sas"
        assert (await create_file(client, target, key, 4)).status_code == 201
        assert (await put_range(client, target, key, 0, b"data")).status_code == 201
        sas = dict(
            parse_qsl(
                generate_file_sas(
                    "signedaccount",
                    "evidence",
                    ["sas"],
                    account_key=key,
                    permission=FileSasPermissions(read=True),
                    expiry=datetime.now(UTC) + timedelta(hours=1),
                )
            )
        )
        response = await client.get(target, params=sas)
        assert response.status_code == 200 and response.content == b"data", response.text
        assert (await client.delete(target, params=sas)).status_code == 403
        response = await client.get(target, params=account_sas(key, srt="sco"))
        assert response.status_code == 200 and response.content == b"data", response.text

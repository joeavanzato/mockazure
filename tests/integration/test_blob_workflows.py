"""Real Blob wire operations preserve bytes, versions and current authority."""

import asyncio
import base64
import hashlib
import hmac
from datetime import UTC, datetime
from email.utils import format_datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import httpx
import pytest

from tests.integration.graph_support import graph_client
from tests.integration.test_storage_authorization import ARGS, provision


def wire(method, url, key, query=None, body=b"", extra=None):
    query = query or {}
    headers = {
        "x-ms-version": "2025-11-05",
        "x-ms-date": format_datetime(datetime.now(UTC), usegmt=True),
    } | (extra or {})
    if body:
        headers.setdefault("Content-Type", "application/octet-stream")
        headers["Content-Length"] = str(len(body))
    values = {k.lower(): v for k, v in headers.items()}
    fields = [method] + [
        values.get(k, "")
        for k in (
            "content-encoding",
            "content-language",
            "content-length",
            "content-md5",
            "content-type",
            "date",
            "if-modified-since",
            "if-match",
            "if-none-match",
            "if-unmodified-since",
            "range",
        )
    ]
    fields[6] = ""
    if fields[3] == "0":
        fields[3] = ""
    raw = urlsplit(str(httpx.URL(url))).path
    account = raw.split("/")[1]
    text = (
        "\n".join(fields)
        + "\n"
        + "".join(k + ":" + v + "\n" for k, v in sorted(values.items()) if k.startswith("x-ms-"))
        + "/"
        + account
        + raw
    )
    text += "".join("\n" + k.lower() + ":" + v for k, v in sorted(query.items()))
    headers["Authorization"] = (
        "SharedKey "
        + account
        + ":"
        + base64.b64encode(
            hmac.new(base64.b64decode(key), text.encode(), hashlib.sha256).digest()
        ).decode()
    )
    return headers


async def call(client, method, url, key, query=None, body=b"", extra=None):
    return await client.request(
        method, url, params=query, content=body, headers=wire(method, url, key, query, body, extra)
    )


async def setup_blob(client, database, settings, tenant, app):
    path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
    state_url = f"/_mock/tenants/{tenant}/cloud/storageState"
    state = (await client.get(state_url)).json()
    state["accounts"][0]["services"] = {
        "blob": {"isVersioningEnabled": True, "deleteRetentionPolicy": {"enabled": True, "days": 7}}
    }
    changed = await client.put(state_url, json=state)
    assert changed.status_code == 200, changed.text
    key = (await client.post(path + "/listKeys", params=ARGS, headers=arm)).json()["keys"][0][
        "value"
    ]
    url = endpoints["blob"] + "evidence"
    created = await call(client, "PUT", url, key, {"restype": "container"})
    assert created.status_code == 201, created.text
    return url, key


async def test_blob_block_and_version_roundtrip(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        target = url + "/folder/data.bin"
        blocks = [
            (base64.b64encode(b"0001").decode(), b"one"),
            (base64.b64encode(b"0002").decode(), b"two"),
        ]
        for block, data in reversed(blocks):
            result = await call(
                client, "PUT", target, key, {"comp": "block", "blockid": block}, data
            )
            assert result.status_code == 201, result.text
        payload = (
            "<BlockList>"
            + "".join("<Latest>" + block + "</Latest>" for block, _ in blocks)
            + "</BlockList>"
        ).encode()
        committed = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "blocklist"},
            payload,
            {"Content-Type": "application/xml"},
        )
        assert committed.status_code == 201, committed.text
        version = committed.headers["x-ms-version-id"]
        data = await call(client, "GET", target, key)
        assert (
            data.content == b"onetwo"
            and hashlib.sha256(data.content).hexdigest() == hashlib.sha256(b"onetwo").hexdigest()
        )
        part = await call(client, "GET", target, key, extra={"Range": "bytes=2-4"})
        assert part.status_code == 206 and part.content == b"etw"
        assert part.headers["Content-Range"] == "bytes 2-4/6"
        overwritten = await call(
            client, "PUT", target, key, body=b"new", extra={"x-ms-blob-type": "BlockBlob"}
        )
        assert overwritten.status_code == 201, overwritten.text
        assert (await call(client, "GET", target, key, {"versionid": version})).content == b"onetwo"
        for name in ("folder/a", "folder/z", "outside"):
            assert (
                await call(
                    client,
                    "PUT",
                    url + "/" + name,
                    key,
                    body=name.encode(),
                    extra={"x-ms-blob-type": "BlockBlob"},
                )
            ).status_code == 201
        names = []
        marker = ""
        while True:
            response = await call(
                client,
                "GET",
                url,
                key,
                {
                    "restype": "container",
                    "comp": "list",
                    "prefix": "folder/",
                    "maxresults": "1",
                    "marker": marker,
                },
            )
            assert response.status_code == 200, response.text
            xml = ET.fromstring(response.content)
            names += [n.text for n in xml.findall("./Blobs/Blob/Name")]
            marker = xml.findtext("NextMarker") or ""
            if not marker:
                break
        assert names == ["folder/a", "folder/data.bin", "folder/z"]
        assert len(names) == len(set(names))


async def test_blob_conditional_race(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        target = url + "/race"
        first = await call(
            client, "PUT", target, key, body=b"old", extra={"x-ms-blob-type": "BlockBlob"}
        )
        assert first.status_code == 201, first.text
        etag = first.headers["ETag"]
        writes = await asyncio.gather(
            *[
                call(
                    client,
                    "PUT",
                    target,
                    key,
                    body=value,
                    extra={"x-ms-blob-type": "BlockBlob", "If-Match": etag},
                )
                for value in (b"one", b"two")
            ]
        )
        assert sorted(r.status_code for r in writes) == [201, 412]
        current = await call(client, "GET", target, key)
        cached = await call(
            client, "GET", target, key, extra={"If-None-Match": current.headers["ETag"]}
        )
        assert cached.status_code == 304 and cached.content == b""
        assert current.content in {b"one", b"two"}


async def test_blob_lease_snapshot_delete(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        target = url + "/leased"
        first = await call(
            client, "PUT", target, key, body=b"original", extra={"x-ms-blob-type": "BlockBlob"}
        )
        assert first.status_code == 201, first.text
        lease = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "lease"},
            extra={"x-ms-lease-action": "acquire", "x-ms-lease-duration": "15"},
        )
        assert lease.status_code == 201, lease.text
        identifier = lease.headers["x-ms-lease-id"]
        assert (
            await call(
                client, "PUT", target, key, body=b"denied", extra={"x-ms-blob-type": "BlockBlob"}
            )
        ).status_code == 412
        assert (
            await call(
                client,
                "PUT",
                target,
                key,
                {"comp": "lease"},
                extra={"x-ms-lease-action": "renew", "x-ms-lease-id": identifier},
            )
        ).status_code == 200
        snapshot = await call(
            client, "PUT", target, key, {"comp": "snapshot"}, extra={"x-ms-lease-id": identifier}
        )
        assert snapshot.status_code == 201, snapshot.text
        stamp = snapshot.headers["x-ms-snapshot"]
        assert (
            await call(
                client,
                "PUT",
                target,
                key,
                body=b"replacement",
                extra={"x-ms-blob-type": "BlockBlob", "x-ms-lease-id": identifier},
            )
        ).status_code == 201
        assert (await call(client, "GET", target, key, {"snapshot": stamp})).content == b"original"
        assert (
            await call(
                client,
                "PUT",
                target,
                key,
                {"comp": "lease"},
                extra={"x-ms-lease-action": "release", "x-ms-lease-id": identifier},
            )
        ).status_code == 200
        assert (await call(client, "DELETE", target, key)).status_code == 409
        deleted = await call(
            client, "DELETE", target, key, extra={"x-ms-delete-snapshots": "include"}
        )
        assert deleted.status_code == 202, deleted.text
        assert (await call(client, "GET", target, key)).status_code == 404
        assert (await call(client, "PUT", target, key, {"comp": "undelete"})).status_code == 200
        assert (await call(client, "GET", target, key, {"snapshot": stamp})).content == b"original"
        restored = await call(client, "GET", target, key)
        # Versioning retains versions rather than implicitly creating a new current blob.
        assert restored.status_code == 404


async def test_stream_upload_hash_and_chunk_boundaries(database, settings):
    from mockazure.content.repository import stream_content
    from mockazure.content.stream_upload import (
        append_stream_chunk,
        begin_stream_upload,
        complete_stream_upload,
    )

    async with graph_client(database, settings) as (_, tenant, *rest):
        content = b"a" * (1024 * 1024 - 3) + b"boundary" + b"z" * 100
        async with database.sessions.begin() as session:
            upload = await begin_stream_upload(session, tenant, None, 2 * 1024 * 1024)
            for start in range(0, len(content), 70001):
                await append_stream_chunk(session, tenant, upload, content[start : start + 70001])
            ref = await complete_stream_upload(session, tenant, upload, "application/octet-stream")
            assert ref.size == len(content) and ref.sha256 == hashlib.sha256(content).hexdigest()
        async with database.sessions() as session:
            parts = [part async for part in stream_content(session, tenant, ref.id)]
            assert max(map(len, parts)) <= 1024 * 1024 and b"".join(parts) == content


async def test_blob_typed_restore_revokes_lease_cursor_and_upload(database, settings):
    from mockazure.content.stream_upload import append_stream_chunk, begin_stream_upload
    from mockazure.providers.storage.errors import StorageError

    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        for name in ("a", "b"):
            assert (
                await call(
                    client,
                    "PUT",
                    url + "/" + name,
                    key,
                    body=name.encode(),
                    extra={"x-ms-blob-type": "BlockBlob"},
                )
            ).status_code == 201
        lease = await call(
            client,
            "PUT",
            url + "/a",
            key,
            {"comp": "lease"},
            extra={"x-ms-lease-action": "acquire", "x-ms-lease-duration": "-1"},
        )
        assert lease.status_code == 201, lease.text
        page = await call(
            client, "GET", url, key, {"restype": "container", "comp": "list", "maxresults": "1"}
        )
        marker = ET.fromstring(page.content).findtext("NextMarker")
        assert marker
        async with database.sessions.begin() as session:
            upload = await begin_stream_upload(session, tenant, 1, 10)
        state_url = f"/_mock/tenants/{tenant}/cloud/storageState"
        exported = await client.get(state_url)
        snapshot = exported.json()
        assert len(snapshot["accounts"][0]["blobs"]) == 2
        assert key not in exported.text and lease.headers["x-ms-lease-id"] not in exported.text
        restored = await client.put(state_url, json=snapshot)
        assert restored.status_code == 200, restored.text
        assert (await call(client, "GET", url + "/a", key)).status_code == 403
        # Admin restore returns fresh account keys; current authorization still applies.
        from tests.integration.graph_support import headers_for

        arm = await headers_for(
            database, settings, tenant, app, None, [], audience="https://management.azure.com/"
        )
        account = snapshot["accounts"][0]["id"]
        newkey = (await client.post(account + "/listKeys", params=ARGS, headers=arm)).json()[
            "keys"
        ][0]["value"]
        assert (await call(client, "GET", url + "/a", newkey)).content == b"a"
        denied = await call(
            client,
            "GET",
            url,
            newkey,
            {"restype": "container", "comp": "list", "maxresults": "1", "marker": marker},
        )
        assert denied.status_code == 400
        denied = await call(
            client,
            "PUT",
            url + "/a",
            newkey,
            body=b"x",
            extra={"x-ms-blob-type": "BlockBlob", "x-ms-lease-id": lease.headers["x-ms-lease-id"]},
        )
        assert denied.status_code == 412
        async with database.sessions.begin() as session:
            with pytest.raises(StorageError):
                await append_stream_chunk(session, tenant, upload, b"x")
        assert (await client.put(state_url, json={"accounts": []})).status_code == 200
        from sqlalchemy import func, select

        from mockazure.content.models import ContentReference

        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ContentReference)
                    .where(
                        ContentReference.tenant_id == tenant,
                        ContentReference.owner_kind.in_(["blob-version", "blob-block"]),
                    )
                )
                == 0
            )


async def test_blob_prune_deleted_version_keeps_live_bytes(database, settings):
    from datetime import timedelta

    from mockazure.storage.lifecycle import prune_storage_accounts

    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        first = await call(
            client, "PUT", url + "/a", key, body=b"old", extra={"x-ms-blob-type": "BlockBlob"}
        )
        second = await call(
            client, "PUT", url + "/a", key, body=b"live", extra={"x-ms-blob-type": "BlockBlob"}
        )
        assert first.status_code == second.status_code == 201
        query = {"versionid": first.headers["x-ms-version-id"]}
        assert (await call(client, "DELETE", url + "/a", key, query)).status_code == 202
        async with database.sessions.begin() as session:
            assert (
                await prune_storage_accounts(
                    session, tenant, datetime.now(UTC) + timedelta(days=8), 1
                )
                == 1
            )
        assert (await call(client, "GET", url + "/a", key, query)).status_code == 404
        assert (await call(client, "GET", url + "/a", key)).content == b"live"


async def test_blob_block_commit_defaults_content_type(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        target = url + "/empty"
        result = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "blocklist"},
            b"<BlockList />",
            {"Content-Type": "application/xml"},
        )
        assert result.status_code == 201, result.text
        got = await call(client, "HEAD", target, key)
        assert got.headers["Content-Type"] == "application/octet-stream"
        assert got.headers["Content-Length"] == "0"


async def test_blob_rejects_behavior_headers_on_wrong_operations(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        target = url + "/wrong"
        result = await call(
            client,
            "PUT",
            target,
            key,
            body=b"data",
            extra={"x-ms-blob-type": "BlockBlob", "x-ms-lease-action": "acquire"},
        )
        assert result.status_code == 400, result.text
        assert (await call(client, "GET", target, key)).status_code == 404
        assert (
            await call(
                client, "PUT", target, key, body=b"data", extra={"x-ms-blob-type": "BlockBlob"}
            )
        ).status_code == 201
        result = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "snapshot"},
            extra={"x-ms-blob-content-type": "text/plain"},
        )
        assert result.status_code == 400


async def test_blob_cached_response_keeps_validators(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        response = await call(
            client, "PUT", url + "/cached", key, body=b"data", extra={"x-ms-blob-type": "BlockBlob"}
        )
        etag = response.headers["ETag"]
        cached = await call(client, "GET", url + "/cached", key, extra={"If-None-Match": etag})
        assert cached.status_code == 304 and cached.content == b""
        assert cached.headers["ETag"] == etag and cached.headers["Last-Modified"]


async def test_blob_invalid_import_rolls_back_bytes_and_authority(database, settings):
    from uuid import uuid4

    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        assert (
            await call(
                client,
                "PUT",
                url + "/kept",
                key,
                body=b"kept",
                extra={"x-ms-blob-type": "BlockBlob"},
            )
        ).status_code == 201
        path = f"/_mock/tenants/{tenant}/cloud/storageState"
        state = (await client.get(path)).json()
        state["accounts"][0]["blobs"][0]["versions"][0]["content_id"] = str(uuid4())
        invalid = await client.put(path, json=state)
        assert invalid.status_code == 422, invalid.text
        assert (await call(client, "GET", url + "/kept", key)).content == b"kept"


async def test_blob_invalid_block_commit_does_not_replace_current(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        target = url + "/kept"
        first = await call(
            client, "PUT", target, key, body=b"kept", extra={"x-ms-blob-type": "BlockBlob"}
        )
        block = base64.b64encode(b"0001").decode()
        assert (
            await call(client, "PUT", target, key, {"comp": "block", "blockid": block}, b"staged")
        ).status_code == 201
        invalid = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "blocklist"},
            b"<BlockList><Committed>MDAwMg==</Committed></BlockList>",
        )
        assert invalid.status_code == 400, invalid.text
        read = await call(client, "GET", target, key)
        assert read.content == b"kept" and read.headers["ETag"] == first.headers["ETag"]
        listed = await call(
            client, "GET", target, key, {"comp": "blocklist", "blocklisttype": "uncommitted"}
        )
        assert block in listed.text
        corrupt = await call(
            client,
            "PUT",
            target,
            key,
            body=b"wrong",
            extra={
                "x-ms-blob-type": "BlockBlob",
                "Content-MD5": base64.b64encode(bytes(16)).decode(),
            },
        )
        assert corrupt.status_code == 400
        assert (await call(client, "GET", target, key)).content == b"kept"


async def test_blob_delimiter_page_does_not_repeat_prefix(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        for name in ("a/one", "a/two", "b/one", "leaf"):
            assert (
                await call(
                    client,
                    "PUT",
                    url + "/" + name,
                    key,
                    body=b"x",
                    extra={"x-ms-blob-type": "BlockBlob"},
                )
            ).status_code == 201
        result = []
        marker = ""
        while True:
            response = await call(
                client,
                "GET",
                url,
                key,
                {
                    "restype": "container",
                    "comp": "list",
                    "delimiter": "/",
                    "maxresults": "1",
                    "marker": marker,
                },
            )
            assert response.status_code == 200, response.text
            xml = ET.fromstring(response.content)
            result.extend(n.text for n in xml.findall("./Blobs/*/Name"))
            marker = xml.findtext("NextMarker") or ""
            if not marker:
                break
        assert result == ["a/", "b/", "leaf"]


async def test_blob_http_oauth_data_roles_and_current_revocation(database, settings):
    from sqlalchemy import delete, select

    from mockazure.identity.models import ServicePrincipal
    from mockazure.providers.arm.models import RoleAssignment
    from tests.integration.graph_support import headers_for
    from tests.integration.test_arm_authorization import assign

    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        assert (
            await call(
                client,
                "PUT",
                url + "/private",
                key,
                body=b"private",
                extra={"x-ms-blob-type": "BlockBlob"},
            )
        ).status_code == 201
        token = await headers_for(
            database, settings, tenant, app, None, [], audience="https://storage.azure.com/"
        )
        token["x-ms-version"] = "2025-11-05"
        assert (await client.get(url + "/private", headers=token)).status_code == 403
        state = (await client.get(f"/_mock/tenants/{tenant}/cloud/storageState")).json()
        account = state["accounts"][0]["id"]
        async with database.sessions() as session:
            principal = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(
            client,
            f"/_mock/tenants/{tenant}/subscriptions/{account.split('/')[2]}",
            principal,
            account,
            "Storage Blob Data Reader",
        )
        result = await client.get(url + "/private", headers=token)
        assert result.status_code == 200 and result.content == b"private"
        async with database.sessions.begin() as session:
            await session.execute(
                delete(RoleAssignment).where(
                    RoleAssignment.tenant_id == tenant, RoleAssignment.client_id == app
                )
            )
        assert (await client.get(url + "/private", headers=token)).status_code == 403


async def test_blob_lease_break_invalidates_old_id(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        target = url + "/lease"
        assert (
            await call(
                client, "PUT", target, key, body=b"first", extra={"x-ms-blob-type": "BlockBlob"}
            )
        ).status_code == 201
        acquired = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "lease"},
            extra={"x-ms-lease-action": "acquire", "x-ms-lease-duration": "-1"},
        )
        assert acquired.status_code == 201, acquired.text
        identifier = acquired.headers["x-ms-lease-id"]
        broken = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "lease"},
            extra={"x-ms-lease-action": "break", "x-ms-lease-break-period": "0"},
        )
        assert broken.status_code == 202 and broken.headers["x-ms-lease-time"] == "0"
        assert (
            await call(
                client,
                "PUT",
                target,
                key,
                {"comp": "lease"},
                extra={"x-ms-lease-action": "renew", "x-ms-lease-id": identifier},
            )
        ).status_code == 409
        assert (
            await call(
                client, "PUT", target, key, body=b"after", extra={"x-ms-blob-type": "BlockBlob"}
            )
        ).status_code == 201


async def test_stream_upload_limit_and_length_fail_atomically(database, settings):
    from mockazure.content.stream_upload import (
        append_stream_chunk,
        begin_stream_upload,
        complete_stream_upload,
    )
    from mockazure.providers.storage.errors import StorageError

    async with graph_client(database, settings) as (_, tenant, *rest):
        async with database.sessions.begin() as session:
            upload = await begin_stream_upload(session, tenant, 4, 4)
            await append_stream_chunk(session, tenant, upload, b"abc")
        with pytest.raises(StorageError):
            async with database.sessions.begin() as session:
                await append_stream_chunk(session, tenant, upload, b"too long")
        with pytest.raises(StorageError):
            async with database.sessions.begin() as session:
                await complete_stream_upload(session, tenant, upload, "application/octet-stream")
        async with database.sessions.begin() as session:
            await append_stream_chunk(session, tenant, upload, b"d")
            result = await complete_stream_upload(
                session, tenant, upload, "application/octet-stream"
            )
            assert result.size == 4 and result.sha256 == hashlib.sha256(b"abcd").hexdigest()


async def test_stream_append_waits_for_reset_and_rejects_old_generation(database, settings):
    from mockazure.cloud.events import lock_cloud_state
    from mockazure.content.stream_upload import append_stream_chunk, begin_stream_upload
    from mockazure.providers.storage.errors import StorageError

    async with graph_client(database, settings) as (_, tenant, *rest):
        async with database.sessions.begin() as session:
            upload = await begin_stream_upload(session, tenant, 1, 1)

        async def append():
            try:
                async with database.sessions.begin() as session:
                    await append_stream_chunk(session, tenant, upload, b"x")
            except StorageError as error:
                return error.status
            return 200

        async with database.sessions() as reset:
            await reset.begin()
            state = await lock_cloud_state(reset, tenant)
            state.generation += 1
            await reset.flush()
            task = asyncio.create_task(append())
            try:
                await asyncio.sleep(0.15)
                early = task.done()
            finally:
                await reset.commit()
            result = await task
        assert not early and result == 409


async def test_blob_account_listing_trailing_slashes(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        account = url.rsplit("/", 1)[0]
        for suffix in ("", "/", "//"):
            result = await call(client, "GET", account + suffix, key, {"comp": "list"})
            assert result.status_code == 200, result.text
            assert ET.fromstring(result.content).findtext("Containers/Container/Name") == "evidence"


async def test_blob_metadata_read_and_lease_precedes_cache_validation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        target = url + "/metadata.bin"
        created = await call(
            client,
            "PUT",
            target,
            key,
            body=b"payload",
            extra={"x-ms-blob-type": "BlockBlob", "x-ms-meta-owner": "collector"},
        )
        assert created.status_code == 201
        version = created.headers["x-ms-version-id"]
        for verb in ("GET", "HEAD"):
            result = await call(
                client, verb, target, key, {"comp": "metadata", "versionid": version}
            )
            assert result.status_code == 200, result.text
            assert result.content == b""
            assert result.headers["x-ms-meta-owner"] == "collector"
            assert result.headers["ETag"] == created.headers["ETag"]
        lease = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "lease"},
            extra={"x-ms-lease-action": "acquire", "x-ms-lease-duration": "-1"},
        )
        assert lease.status_code == 201
        result = await call(
            client,
            "GET",
            target,
            key,
            extra={
                "If-None-Match": created.headers["ETag"],
                "x-ms-lease-id": "11111111-1111-1111-1111-111111111111",
            },
        )
        assert result.status_code == 412, result.text


async def test_blob_sas_create_cannot_overwrite_or_mutate_metadata(database, settings):
    from datetime import timedelta
    from urllib.parse import parse_qsl

    from azure.storage.blob import BlobSasPermissions, generate_blob_sas

    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        client.headers.pop("Authorization", None)
        target = url + "/create-only"
        sas = dict(
            parse_qsl(
                generate_blob_sas(
                    "signedaccount",
                    "evidence",
                    "create-only",
                    account_key=key,
                    permission=BlobSasPermissions(create=True),
                    expiry=datetime.now(UTC) + timedelta(hours=1),
                )
            )
        )
        first = await client.put(
            target,
            params=sas,
            content=b"first",
            headers={"x-ms-version": "2025-11-05", "x-ms-blob-type": "BlockBlob"},
        )
        assert first.status_code == 201, first.text
        second = await client.put(
            target,
            params=sas,
            content=b"replaced",
            headers={"x-ms-version": "2025-11-05", "x-ms-blob-type": "BlockBlob"},
        )
        assert second.status_code == 403, second.text
        changed = await client.put(
            target,
            params=sas | {"comp": "metadata"},
            headers={"x-ms-version": "2025-11-05", "x-ms-meta-owner": "forbidden"},
        )
        assert changed.status_code == 403, changed.text
        for query, data in [
            ({"comp": "block", "blockid": "MDAwMQ=="}, b"block"),
            ({"comp": "blocklist"}, b"<BlockList/>"),
        ]:
            result = await client.put(
                target, params=sas | query, content=data, headers={"x-ms-version": "2025-11-05"}
            )
            assert result.status_code == 403, result.text
        assert (await call(client, "GET", target, key)).content == b"first"


async def test_blob_account_and_service_sas_reads_and_signed_overrides(database, settings):
    from datetime import timedelta
    from urllib.parse import parse_qsl

    from azure.storage.blob import BlobSasPermissions, generate_blob_sas

    from tests.integration.test_storage_authorization import account_sas

    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        client.headers.pop("Authorization", None)
        target = url + "/download"
        assert (
            await call(
                client, "PUT", target, key, body=b"bytes", extra={"x-ms-blob-type": "BlockBlob"}
            )
        ).status_code == 201
        sas = dict(
            parse_qsl(
                generate_blob_sas(
                    "signedaccount",
                    "evidence",
                    "download",
                    account_key=key,
                    permission=BlobSasPermissions(read=True),
                    expiry=datetime.now(UTC) + timedelta(hours=1),
                    content_type="application/custom",
                    content_disposition="attachment; filename=evidence.bin",
                    cache_control="no-store",
                )
            )
        )
        downloaded = await client.get(target, params=sas)
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == b"bytes"
        assert downloaded.headers["Content-Type"] == "application/custom"
        assert downloaded.headers["Cache-Control"] == "no-store"
        assert downloaded.headers["Content-Disposition"] == "attachment; filename=evidence.bin"
        assert (await client.get(target, params=sas | {"rsct": "tampered"})).status_code == 403
        response = await client.get(target, params=account_sas(key, srt="sco"))
        assert response.status_code == 200 and response.content == b"bytes", response.text


async def test_blob_migration_upgrade_preserves_namespace_and_guards_content(database, settings):
    from alembic import command
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError

    from mockazure.db.migrations import configuration

    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
        try:
            with engine.begin() as connection:
                config = configuration()
                config.attributes["connection"] = connection
                command.downgrade(config, "061")
                assert connection.scalar(text("SELECT count(*) FROM storage_namespaces")) == 1
                command.upgrade(config, "head")
                assert connection.scalar(text("SELECT count(*) FROM storage_namespaces")) == 1
            target = url + "/persistent"
            assert (
                await call(
                    client,
                    "PUT",
                    target,
                    key,
                    body=b"preserve",
                    extra={"x-ms-blob-type": "BlockBlob"},
                )
            ).status_code == 201
            with pytest.raises(DBAPIError, match="Export and reset Blob content"):
                with engine.begin() as connection:
                    config = configuration()
                    config.attributes["connection"] = connection
                    command.downgrade(config, "061")
            assert (await call(client, "GET", target, key)).content == b"preserve"
        finally:
            engine.dispose()


async def test_blob_profile_versions_and_snapshot_without_lease(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        target = url + "/profile"
        for version in ("2018-11-09", "2025-01-05"):
            result = await call(
                client,
                "PUT",
                target,
                key,
                body=b"unsupported",
                extra={"x-ms-blob-type": "BlockBlob", "x-ms-version": version},
            )
            assert result.status_code == 400, result.text
        created = await call(
            client,
            "PUT",
            target,
            key,
            body=b"content",
            extra={
                "x-ms-blob-type": "BlockBlob",
                "Content-Language": "en",
                "Cache-Control": "private",
                "Content-Disposition": "attachment",
            },
        )
        assert created.status_code == 201
        props = await call(client, "HEAD", target, key)
        assert props.headers["Content-Language"] == "en"
        assert props.headers["Cache-Control"] == "private"
        assert props.headers["Content-Disposition"] == "attachment"
        lease = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "lease"},
            extra={"x-ms-lease-action": "acquire", "x-ms-lease-duration": "-1"},
        )
        assert lease.status_code == 201
        snap = await call(client, "PUT", target, key, {"comp": "snapshot"})
        assert snap.status_code == 201, snap.text
        changed = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "snapshot"},
            extra={"x-ms-lease-id": "11111111-1111-1111-1111-111111111111"},
        )
        assert changed.status_code == 412
        saved = await call(client, "GET", target, key, {"snapshot": snap.headers["x-ms-snapshot"]})
        assert saved.content == b"content"
        assert saved.headers["x-ms-lease-status"] == "unlocked"
        deleted = await call(
            client, "DELETE", target, key, {"snapshot": snap.headers["x-ms-snapshot"]}
        )
        assert deleted.status_code == 202, deleted.text
        assert (
            await call(
                client, "PUT", target, key, body=b"blocked", extra={"x-ms-blob-type": "BlockBlob"}
            )
        ).status_code == 412

"""Storage durability, generation fences and one fact per committed action."""

import hashlib
import json
import tracemalloc
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy import func, select

from mockazure.cloud.events import append_service_event
from mockazure.cloud.lifecycle import export_cloud, import_cloud
from mockazure.cloud.models import CloudEvent
from mockazure.cloud.types import ServiceEvent
from mockazure.content.repository import stream_content
from mockazure.content.stream_upload import (
    append_stream_chunk,
    begin_stream_upload,
    complete_stream_upload,
)
from mockazure.db.engine import close_database, create_database
from mockazure.identity.keys import read_wrapping_key
from mockazure.providers.storage.errors import StorageError
from mockazure.storage.blob import put_blob
from mockazure.storage.blob_models import BlobVersion
from mockazure.storage.file_models import FileNode
from mockazure.storage.files import create_node, write_file_range
from mockazure.storage.types import StorageAccess
from tests.integration.graph_support import graph_client
from tests.integration.test_blob_workflows import call
from tests.integration.test_files_rest_workflows import create_file
from tests.integration.test_storage_authorization import ARGS, provision

CHUNK = 1048576


async def test_storage_restart_restore(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        account, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        for service, kind in [("blob", "container"), ("file", "share")]:
            assert (
                await call(client, "PUT", endpoints[service] + "durable", key, {"restype": kind})
            ).status_code == 201
        access = StorageAccess(tenant, account.lower(), "shared_key", None, frozenset({"*"}))
        size = 256 * CHUNK
        chunk = bytes(range(256)) * 4096
        expected = hashlib.sha256()
        tracemalloc.start()
        reopened = None
        try:
            async with database.sessions.begin() as session:
                upload = await begin_stream_upload(session, tenant, size, size)
                for _ in range(256):
                    await append_stream_chunk(session, tenant, upload, chunk)
                    expected.update(chunk)
                await put_blob(
                    session,
                    access,
                    "durable",
                    "large.bin",
                    upload,
                    {"properties": {"metadata": {"label": "durable"}}},
                    datetime.now(UTC),
                )
                await create_node(
                    session,
                    access,
                    "durable",
                    "large.bin",
                    False,
                    size,
                    {"metadata": {"label": "file-durable"}},
                    {},
                    datetime.now(UTC),
                )
                file = await write_file_range(
                    session,
                    access,
                    "durable",
                    "large.bin",
                    CHUNK - 2,
                    b"boundary",
                    8,
                    {},
                    datetime.now(UTC),
                )
            # Dispose the connection pool, then use a separately constructed authority.
            await database.engine.dispose()
            reopened = create_database(settings)
            async with reopened.sessions.begin() as session:
                state = await export_cloud(session, tenant)
                assert key not in json.dumps(state)
                session.info["storage_cipher"] = read_wrapping_key(settings.data_dir)
                await import_cloud(session, tenant, state)
            hashes = []
            async with reopened.sessions() as session:
                for model in (BlobVersion, FileNode):
                    row = await session.scalar(
                        select(model).where(
                            model.tenant_id == tenant, model.content_id.is_not(None)
                        )
                    )
                    observed = hashlib.sha256()
                    total = 0
                    async for part in stream_content(session, tenant, row.content_id):
                        assert len(part) <= CHUNK
                        observed.update(part)
                        total += len(part)
                    assert total == size
                    assert row.properties["metadata"]["label"] == (
                        "durable" if model == BlobVersion else "file-durable"
                    )
                    hashes.append(observed.hexdigest())
            assert hashes[0] == expected.hexdigest()
            async with reopened.sessions() as session:
                observed = hashlib.sha256()
                async for part in stream_content(session, tenant, file.content_id):
                    observed.update(part)
                assert hashes[1] == observed.hexdigest()
            _, peak = tracemalloc.get_traced_memory()
            assert peak < 32 * CHUNK, f"Unbounded Python heap: {peak}"
            print(
                {
                    "bytesPerObject": size,
                    "blobSha256": hashes[0],
                    "fileSha256": hashes[1],
                    "pythonPeakBytes": peak,
                }
            )
        finally:
            tracemalloc.stop()
            if reopened:
                await close_database(reopened)


async def test_storage_reset_fences_old_work(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        account, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["file"] + "durable"
        assert (await call(client, "PUT", url, key, {"restype": "share"})).status_code == 201
        for name in ["a", "b"]:
            assert (await create_file(client, url + "/" + name, key, 1)).status_code == 201
        args = {"restype": "directory", "comp": "list", "maxresults": "1"}
        page = await call(client, "GET", url, key, args)
        marker = ET.fromstring(page.content).findtext("NextMarker")
        assert marker
        async with database.sessions.begin() as session:
            upload = await begin_stream_upload(session, tenant, 1, CHUNK)
            await append_stream_chunk(session, tenant, upload, b"x")
        base = f"/_mock/tenants/{tenant}/cloud"
        state = (await client.get(base + "/state")).json()
        result = await client.post(
            base + "/reset", json={"expected_generation": state["generation"]}
        )
        assert result.status_code == 200, result.text
        assert (await client.put(base + "/state", json=state)).status_code == 200
        newkey = (await client.post(account + "/listKeys", params=ARGS, headers=arm)).json()[
            "keys"
        ][0]["value"]
        assert (
            await call(client, "GET", url, newkey, args | {"marker": marker})
        ).status_code == 400
        for operation in ("append", "complete"):
            async with database.sessions.begin() as session:
                with pytest.raises(StorageError) as error:
                    if operation == "append":
                        await append_stream_chunk(session, tenant, upload, b"x")
                    else:
                        await complete_stream_upload(
                            session, tenant, upload, "application/octet-stream"
                        )
                assert error.value.status == 409


async def test_storage_diagnostic_fact_once(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["blob"] + "durable"
        assert (await call(client, "PUT", url, key, {"restype": "container"})).status_code == 201
        assert (
            await call(
                client,
                "PUT",
                url + "/evidence",
                key,
                body=b"bytes",
                extra={"x-ms-blob-type": "BlockBlob"},
            )
        ).status_code == 201
        async with database.sessions.begin() as session:
            row = await session.scalar(
                select(CloudEvent).where(
                    CloudEvent.tenant_id == tenant, CloudEvent.operation == "PutBlob"
                )
            )
            assert row.payload["outcome"] == "Succeeded"
            assert row.payload["name"] == "durable/evidence"
            assert key not in json.dumps(row.payload)
            event = ServiceEvent(
                **{key: getattr(row, key) for key in ServiceEvent.__dataclass_fields__}
            )
            await append_service_event(session, event)
            await append_service_event(session, event)
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(CloudEvent)
                    .where(CloudEvent.tenant_id == tenant, CloudEvent.id == event.id)
                )
                == 1
            )
        bad = await call(
            client,
            "PUT",
            url + "/evidence",
            key,
            body=b"rejected",
            extra={"x-ms-blob-type": "BlockBlob", "If-Match": '"stale"'},
        )
        assert bad.status_code == 412
        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(CloudEvent)
                    .where(CloudEvent.tenant_id == tenant, CloudEvent.operation == "PutBlob")
                )
                == 1
            )


async def test_storage_original_powershell_blob_version(database, settings):
    import base64

    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["blob"] + "durable"
        target = url + "/legacy"
        assert (await call(client, "PUT", url, key, {"restype": "container"})).status_code == 201
        version = {"x-ms-version": "2019-07-07"}
        uploaded = await call(
            client,
            "PUT",
            target,
            key,
            body=b"legacy",
            extra=version | {"x-ms-blob-type": "BlockBlob"},
        )
        assert uploaded.status_code == 201, uploaded.text
        assert (await call(client, "GET", target, key, extra=version)).content == b"legacy"
        assert (await call(client, "HEAD", target, key, extra=version)).headers[
            "x-ms-version"
        ] == "2019-07-07"
        modern = await call(client, "HEAD", target, key, extra={"x-ms-version": "2025-01-05"})
        assert modern.status_code == 200, modern.headers
        identifier = base64.b64encode(b"block").decode()
        assert (
            await call(
                client,
                "PUT",
                target,
                key,
                {"comp": "block", "blockid": identifier},
                b"block-data",
                version,
            )
        ).status_code == 201
        body = f"<BlockList><Latest>{identifier}</Latest></BlockList>".encode()
        assert (
            await call(client, "PUT", target, key, {"comp": "blocklist"}, body, version)
        ).status_code == 201
        assert (await call(client, "GET", target, key, extra=version)).content == b"block-data"
        assert (
            await call(
                client, "GET", url, key, {"restype": "container", "comp": "list"}, extra=version
            )
        ).status_code == 200
        assert (
            await call(client, "GET", target, key, {"versionid": "arbitrary"}, extra=version)
        ).status_code == 400
        assert (
            await call(client, "PUT", target, key, {"comp": "snapshot"}, extra=version)
        ).status_code == 400


async def test_storage_original_powershell_file_version(database, settings):
    from tests.integration.test_files_rest_workflows import put_range

    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["file"] + "durable"
        target = url + "/legacy"
        assert (await call(client, "PUT", url, key, {"restype": "share"})).status_code == 201
        version = {"x-ms-version": "2019-07-07"}
        required = {
            "x-ms-file-permission": "Inherit",
            "x-ms-file-attributes": "None",
            "x-ms-file-creation-time": "Now",
            "x-ms-file-last-write-time": "Now",
        }
        result = await create_file(
            client, target, key, 6, version | required | {"x-ms-type": "File"}
        )
        assert result.status_code == 201, result.text
        assert (
            await put_range(
                client, target, key, 0, b"legacy", extra=version | {"x-ms-write": "Update"}
            )
        ).status_code == 201
        assert (await call(client, "GET", target, key, extra=version)).content == b"legacy"
        assert (await call(client, "HEAD", target, key, extra=version)).status_code == 200
        assert (
            await call(client, "HEAD", target, key, extra={"x-ms-version": "2025-01-05"})
        ).status_code == 200
        assert (await create_file(client, url + "/missing", key, 1, version)).status_code == 400
        assert (
            await call(
                client, "HEAD", target, key, extra=version | {"x-ms-file-request-intent": "backup"}
            )
        ).status_code == 400
        assert (
            await call(
                client, "GET", url, key, {"restype": "directory", "comp": "list"}, extra=version
            )
        ).status_code == 400


async def test_storage_legacy_share_delete_compatibility(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["file"] + "legacyshare"
        assert (await call(client, "PUT", url, key, {"restype": "share"})).status_code == 201
        deleted = await call(
            client, "DELETE", url, key, {"restype": "share"}, extra={"x-ms-version": "2025-01-05"}
        )
        assert deleted.status_code == 202, deleted.text


async def test_storage_original_sdk_staged_metadata_commits_only_at_block_list(database, settings):
    import base64

    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["blob"] + "stagedmetadata"
        assert (await call(client, "PUT", url, key, {"restype": "container"})).status_code == 201
        target = url + "/binary.bin"
        assert (
            await call(
                client,
                "PUT",
                target,
                key,
                body=b"old",
                extra={"x-ms-blob-type": "BlockBlob", "x-ms-meta-writer": "original"},
            )
        ).status_code == 201
        identifier = base64.b64encode(b"one").decode()
        staged = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "block", "blockid": identifier},
            b"new",
            {"x-ms-meta-writer": "sdk-carried"},
        )
        assert staged.status_code == 201, staged.text
        assert (await call(client, "HEAD", target, key)).headers["x-ms-meta-writer"] == "original"
        body = f"<BlockList><Latest>{identifier}</Latest></BlockList>".encode()
        committed = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "blocklist"},
            body,
            {"x-ms-meta-writer": "committed"},
        )
        assert committed.status_code == 201, committed.text
        assert (await call(client, "HEAD", target, key)).headers["x-ms-meta-writer"] == "committed"
        assert (await call(client, "GET", target, key)).content == b"new"

"""Regressions from the phase C independent review."""

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl
from uuid import uuid4
from xml.etree import ElementTree as ET

import httpx
import pytest
from sqlalchemy import select, text, update

from mockazure.storage.blob_lifecycle import prune_blobs
from mockazure.storage.blob_models import Blob, BlobBlock
from tests.integration.graph_support import graph_client
from tests.integration.test_blob_workflows import call, setup_blob, wire
from tests.integration.test_files_rest_workflows import create_file
from tests.integration.test_storage_authorization import provision


async def stage(client, target, key, identifier, data=b"old"):
    block = base64.b64encode(identifier.encode()).decode()
    result = await call(client, "PUT", target, key, {"comp": "block", "blockid": block}, data)
    assert result.status_code == 201, result.text
    return block


async def test_put_blob_discards_old_staging(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        target = url + "/discard"
        block = await stage(client, target, key, "one")
        assert (
            await call(
                client, "PUT", target, key, body=b"new", extra={"x-ms-blob-type": "BlockBlob"}
            )
        ).status_code == 201
        listing = await call(
            client, "GET", target, key, {"comp": "blocklist", "blocklisttype": "uncommitted"}
        )
        assert ET.fromstring(listing.content).findall("./UncommittedBlocks/Block") == []
        result = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "blocklist"},
            f"<BlockList><Uncommitted>{block}</Uncommitted></BlockList>".encode(),
        )
        assert result.status_code == 400
        assert (await call(client, "GET", target, key)).content == b"new"


async def test_finite_lease_default_break_keeps_remaining_time(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        target = url + "/lease"
        assert (
            await call(
                client, "PUT", target, key, body=b"old", extra={"x-ms-blob-type": "BlockBlob"}
            )
        ).status_code == 201
        assert (
            await call(
                client,
                "PUT",
                target,
                key,
                {"comp": "lease"},
                extra={"x-ms-lease-action": "acquire", "x-ms-lease-duration": "60"},
            )
        ).status_code == 201
        result = await call(
            client, "PUT", target, key, {"comp": "lease"}, extra={"x-ms-lease-action": "break"}
        )
        assert result.status_code == 202
        assert 0 < int(result.headers["x-ms-lease-time"]) <= 60
        assert (await call(client, "HEAD", target, key)).headers["x-ms-lease-state"] == "breaking"


async def test_acquire_retry_matching_proposed_lease_id(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        target = url + "/retry"
        assert (
            await call(
                client, "PUT", target, key, body=b"old", extra={"x-ms-blob-type": "BlockBlob"}
            )
        ).status_code == 201
        headers = {
            "x-ms-lease-action": "acquire",
            "x-ms-lease-duration": "60",
            "x-ms-proposed-lease-id": str(uuid4()),
        }
        assert (
            await call(client, "PUT", target, key, {"comp": "lease"}, extra=headers)
        ).status_code == 201
        result = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "lease"},
            extra=headers | {"x-ms-lease-duration": "30"},
        )
        assert result.status_code == 201, result.text
        assert result.headers["x-ms-lease-id"] == headers["x-ms-proposed-lease-id"]
        assert (
            await call(
                client,
                "PUT",
                target,
                key,
                {"comp": "lease"},
                extra=headers | {"x-ms-proposed-lease-id": str(uuid4())},
            )
        ).status_code == 409


@pytest.mark.parametrize("selector", ["versionid", "snapshot"])
async def test_block_list_unknown_historical_selector_is_not_staging(database, settings, selector):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        target = url + "/missingversion"
        assert (
            await call(
                client, "PUT", target, key, body=b"old", extra={"x-ms-blob-type": "BlockBlob"}
            )
        ).status_code == 201
        result = await call(
            client,
            "GET",
            target,
            key,
            {"comp": "blocklist", selector: "2000-01-01T00:00:00.0000000Z"},
        )
        assert result.status_code == 404, result.text


async def test_snapshot_block_list_excludes_live_staging(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        target = url + "/snapshot"
        block = await stage(client, target, key, "one")
        assert (
            await call(
                client,
                "PUT",
                target,
                key,
                {"comp": "blocklist"},
                f"<BlockList><Latest>{block}</Latest></BlockList>".encode(),
            )
        ).status_code == 201
        snap = await call(client, "PUT", target, key, {"comp": "snapshot"})
        assert snap.status_code == 201
        await stage(client, target, key, "two", b"new")
        result = await call(
            client,
            "GET",
            target,
            key,
            {
                "comp": "blocklist",
                "blocklisttype": "all",
                "snapshot": snap.headers["x-ms-snapshot"],
            },
        )
        assert result.status_code == 200
        root = ET.fromstring(result.content)
        assert root.findall("./UncommittedBlocks/Block") == []
        assert root.findtext("./CommittedBlocks/Block/Name") == block


async def test_block_pruning_tracks_blob_upload_activity(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        target = url + "/active"
        first = await stage(client, target, key, "one")
        second = await stage(client, target, key, "two")
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            await session.execute(
                update(BlobBlock)
                .where(BlobBlock.tenant_id == tenant, BlobBlock.block_id == first)
                .values(created_at=now - timedelta(days=8))
            )
            await session.execute(
                update(BlobBlock)
                .where(BlobBlock.tenant_id == tenant, BlobBlock.block_id == second)
                .values(created_at=now - timedelta(days=2))
            )
            assert await prune_blobs(session, tenant, now, 100) == 0
            assert (
                len(
                    list(
                        await session.scalars(
                            select(BlobBlock).where(BlobBlock.tenant_id == tenant)
                        )
                    )
                )
                == 2
            )
            await session.execute(
                update(BlobBlock)
                .where(BlobBlock.tenant_id == tenant)
                .values(created_at=now - timedelta(days=8))
            )
            await session.execute(
                update(Blob).where(Blob.tenant_id == tenant).values(changed_at=now)
            )
            assert await prune_blobs(session, tenant, now, 100) == 2


@pytest.mark.parametrize("surface", ["namespace", "blob", "file"])
async def test_sas_expiry_is_checked_after_storage_lock(database, settings, monkeypatch, surface):
    from azure.storage.blob import AccountSasPermissions, ResourceTypes, generate_account_sas

    from mockazure.providers.storage import blob_routes, file_routes, routes

    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        service = "file" if surface == "file" else "blob"
        url = endpoints[service] + "expiry"
        assert (
            await call(
                client, "PUT", url, key, {"restype": "share" if service == "file" else "container"}
            )
        ).status_code == 201
        if surface == "file":
            assert (await create_file(client, url + "/object", key, 0)).status_code == 201
        elif surface == "blob":
            assert (
                await call(
                    client,
                    "PUT",
                    url + "/object",
                    key,
                    body=b"x",
                    extra={"x-ms-blob-type": "BlockBlob"},
                )
            ).status_code == 201
        clock = [datetime.now(UTC)]
        sas = generate_account_sas(
            "signedaccount",
            key,
            resource_types=ResourceTypes(service=True, container=True, object=True),
            permission=AccountSasPermissions(read=True),
            expiry=clock[0] + timedelta(seconds=5),
            services=service[0],
        )
        module = {"namespace": routes, "blob": blob_routes, "file": file_routes}[surface]

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock[0]

        lock = module.lock_accounts

        async def delayed(session, owner):
            await lock(session, owner)
            clock[0] += timedelta(seconds=10)

        monkeypatch.setattr(module, "datetime", Clock)
        monkeypatch.setattr(module, "lock_accounts", delayed)
        client.headers.pop("Authorization", None)
        query = dict(parse_qsl(sas))
        if surface == "namespace":
            query["restype"] = "container"
        result = await client.get(
            url if surface == "namespace" else url + "/object",
            params=query,
            headers={"x-ms-version": "2025-11-05"},
        )
        assert result.status_code == 403, result.text


@pytest.mark.parametrize("service", ["blob", "file"])
@pytest.mark.parametrize("direction", ["upload", "download"])
async def test_paused_network_does_not_hold_global_storage_lock(
    database, settings, monkeypatch, service, direction
):
    from mockazure.providers.storage.blob_routes import TransactionalStream

    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints[service] + "independent"
        assert (
            await call(
                client, "PUT", url, key, {"restype": "share" if service == "file" else "container"}
            )
        ).status_code == 201
        target = url + "/data"
        if service == "file":
            assert (await create_file(client, target, key, 2)).status_code == 201
        else:
            assert (
                await call(
                    client, "PUT", target, key, body=b"xx", extra={"x-ms-blob-type": "BlockBlob"}
                )
            ).status_code == 201
        paused, release = asyncio.Event(), asyncio.Event()
        if direction == "download":
            original = TransactionalStream.__call__

            async def guarded(self, scope, receive, send):
                async def slow_send(message):
                    if message["type"] == "http.response.body" and message.get("body"):
                        paused.set()
                        await release.wait()
                    await send(message)

                await original(self, scope, receive, slow_send)

            monkeypatch.setattr(TransactionalStream, "__call__", guarded)
            pending = asyncio.create_task(call(client, "GET", target, key))
        else:

            class SlowBody(httpx.AsyncByteStream):
                async def __aiter__(self):
                    yield b"x"
                    paused.set()
                    await release.wait()
                    yield b"x"

            query = {"comp": "range"} if service == "file" else {}
            extra = (
                {"x-ms-write": "update", "x-ms-range": "bytes=0-1"}
                if service == "file"
                else {"x-ms-blob-type": "BlockBlob"}
            )
            pending = asyncio.create_task(
                client.put(
                    target,
                    params=query,
                    content=SlowBody(),
                    headers=wire("PUT", target, key, query, b"xx", extra),
                )
            )
        try:
            await asyncio.wait_for(paused.wait(), 10)
            async with database.sessions.begin() as session:
                unlocked = await session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(726385210058)")
                )
            assert unlocked, "Network peer retained the instance-global Storage lock"
        finally:
            release.set()
            result = await pending
        assert result.status_code in {200, 201}, result.text


async def test_upload_cannot_use_a_lease_expired_during_body_reception(
    database, settings, monkeypatch
):
    from mockazure.providers.storage import blob_routes

    async with graph_client(database, settings) as (client, tenant, app, user):
        url, key = await setup_blob(client, database, settings, tenant, app)
        target = url + "/slowlease"
        assert (
            await call(
                client, "PUT", target, key, body=b"old", extra={"x-ms-blob-type": "BlockBlob"}
            )
        ).status_code == 201
        clock = [datetime.now(UTC)]

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock[0]

        monkeypatch.setattr(blob_routes, "datetime", Clock)
        lease = await call(
            client,
            "PUT",
            target,
            key,
            {"comp": "lease"},
            extra={"x-ms-lease-action": "acquire", "x-ms-lease-duration": "15"},
        )
        assert lease.status_code == 201

        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"x"
                clock[0] += timedelta(seconds=20)
                yield b"x"

        headers = wire(
            "PUT",
            target,
            key,
            body=b"xx",
            extra={"x-ms-blob-type": "BlockBlob", "x-ms-lease-id": lease.headers["x-ms-lease-id"]},
        )
        result = await client.put(target, content=SlowBody(), headers=headers)
        assert result.status_code == 412, result.text
        assert (await call(client, "GET", target, key)).content == b"old"

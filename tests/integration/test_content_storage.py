import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from mockazure.admin.errors import AdminError
from mockazure.content.repository import (
    begin_upload,
    export_manifest,
    finalize_upload,
    pin_content,
    prune_content,
    put_chunk,
    release_content,
    stream_content,
    validate_manifest,
)
from tests.integration.graph_support import graph_client

CHUNK = 1024 * 1024


async def upload(database, tenant, data):
    async with database.sessions.begin() as session:
        identifier = await begin_upload(
            session, tenant, len(data), hashlib.sha256(data).hexdigest(), "application/octet-stream"
        )
        for ordinal, start in enumerate(range(0, len(data), CHUNK)):
            await put_chunk(session, tenant, identifier, ordinal, data[start : start + CHUNK])
        return await finalize_upload(session, tenant, identifier)


async def test_chunk_retry_and_finalize_hash(database, settings):
    async with graph_client(database, settings) as (_, tenant, *_):
        async with database.sessions.begin() as session:
            identifier = await begin_upload(
                session, tenant, 3, hashlib.sha256(b"abc").hexdigest(), "application/octet-stream"
            )
            await put_chunk(session, tenant, identifier, 0, b"bad")
            await put_chunk(session, tenant, identifier, 0, b"bad")
        async with database.sessions.begin() as session:
            with pytest.raises(AdminError) as error:
                await put_chunk(session, tenant, identifier, 0, b"abc")
            assert error.value.status == 409
        async with database.sessions.begin() as session:
            with pytest.raises(AdminError) as error:
                await finalize_upload(session, tenant, identifier)
            assert error.value.status == 422
        first = await upload(database, tenant, b"abc")
        second = await upload(database, tenant, b"abc")
        assert first.id == second.id
        assert first.sha256 == hashlib.sha256(b"abc").hexdigest()


async def test_unfinished_upload_not_readable(database, settings):
    async with graph_client(database, settings) as (_, tenant, *_):
        async with database.sessions.begin() as session:
            identifier = await begin_upload(
                session, tenant, 3, hashlib.sha256(b"abc").hexdigest(), "text/plain"
            )
            await put_chunk(session, tenant, identifier, 0, b"abc")
        async with database.sessions() as session:
            with pytest.raises(AdminError) as error:
                _ = [part async for part in stream_content(session, tenant, identifier)]
            assert error.value.status == 404


async def test_cross_tenant_content_denied(database, settings):
    async with graph_client(database, settings) as (_, tenant, *_):
        ref = await upload(database, tenant, b"private")
        async with database.sessions() as session:
            with pytest.raises(AdminError) as error:
                _ = [part async for part in stream_content(session, uuid4(), ref.id)]
            assert error.value.status == 404
            with pytest.raises(AdminError):
                await pin_content(session, uuid4(), ref.id, "test", "foreign")


async def test_stream_range_boundaries(database, settings):
    async with graph_client(database, settings) as (_, tenant, *_):
        data = b"a" * CHUNK + b"bc"
        ref = await upload(database, tenant, data)
        async with database.sessions() as session:
            chunks = [part async for part in stream_content(session, tenant, ref.id)]
            assert max(map(len, chunks)) <= CHUNK
            assert b"".join(chunks) == data
            assert (
                b"".join(
                    [
                        part
                        async for part in stream_content(
                            session, tenant, ref.id, CHUNK - 1, CHUNK + 1
                        )
                    ]
                )
                == b"ab"
            )
            with pytest.raises(AdminError) as error:
                _ = [
                    part
                    async for part in stream_content(
                        session, tenant, ref.id, len(data), len(data) + 1
                    )
                ]
            assert error.value.status == 416


async def test_restart_and_reference_aware_prune(database, settings):
    async with graph_client(database, settings) as (_, tenant, *_):
        ref = await upload(database, tenant, b"retained")
        async with database.sessions.begin() as session:
            await pin_content(session, tenant, ref.id, "message-version", "revision-1")
            manifest = await export_manifest(session, tenant)
            await validate_manifest(session, tenant, manifest)
        from mockazure.db.engine import close_database, create_database

        reopened = create_database(settings)
        try:
            async with reopened.sessions.begin() as session:
                result = await prune_content(
                    session, tenant, datetime.now(UTC) + timedelta(days=2), 10
                )
                assert result["deleted"] == 0
                assert (
                    b"".join([part async for part in stream_content(session, tenant, ref.id)])
                    == b"retained"
                )
                await release_content(session, tenant, "message-version", "revision-1")
            async with reopened.sessions.begin() as session:
                result = await prune_content(
                    session, tenant, datetime.now(UTC) + timedelta(days=2), 10
                )
                assert result["deleted"] == 1
        finally:
            await close_database(reopened)


async def test_admin_upload_download_manifest_and_reset(database, settings):
    async with graph_client(database, settings) as (client, tenant, *_):
        base = f"/_mock/tenants/{tenant}/content"
        data = b"raw\x00bytes"
        create = await client.post(
            base + "/uploads",
            json={
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "media_type": "application/octet-stream",
            },
        )
        assert create.status_code == 201, create.text
        identifier = create.json()["uploadId"]
        result = await client.put(base + f"/uploads/{identifier}/chunks/0", content=data)
        assert result.status_code == 204, result.text
        complete = await client.post(base + f"/uploads/{identifier}/complete")
        assert complete.status_code == 200, complete.text
        content_id = complete.json()["id"]
        raw = await client.get(
            base + f"/objects/{content_id}/$value", headers={"Range": "bytes=3-3"}
        )
        assert raw.status_code == 206, raw.text
        assert raw.content == b"\x00"
        assert raw.headers["Content-Range"] == "bytes 3-3/9"
        assert (
            await client.get(base + f"/objects/{content_id}/$value", headers={"Range": "bytes=9-"})
        ).status_code == 416
        manifest = await client.get(base + "/manifest")
        assert manifest.status_code == 200, manifest.text
        assert manifest.json()["content"][0]["sha256"] == hashlib.sha256(data).hexdigest()
        validate = await client.post(base + "/validate", json=manifest.json())
        assert validate.status_code == 200, validate.text
        async with database.sessions.begin() as session:
            await pin_content(session, tenant, UUID(content_id), "test", "retained")
        assert (await client.post(base + "/reset")).status_code == 409
        async with database.sessions.begin() as session:
            await release_content(session, tenant, "test", "retained")
        assert (await client.post(base + "/reset")).status_code == 200
        assert (await client.get(base + f"/objects/{content_id}/$value")).status_code == 404
        recreated = await client.post(
            base + "/uploads",
            json={
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "media_type": "application/octet-stream",
            },
        )
        new_id = recreated.json()["uploadId"]
        assert (
            await client.put(base + f"/uploads/{new_id}/chunks/0", content=data)
        ).status_code == 204
        assert (await client.post(base + f"/uploads/{new_id}/complete")).status_code == 200
        restored = await client.post(
            base + "/validate", json={**manifest.json(), "mapping": {content_id: new_id}}
        )
        assert restored.status_code == 200, restored.text
        tampered = manifest.json()
        tampered["content"][0]["sha256"] = "0" * 64
        assert (
            await client.post(
                base + "/validate", json={**tampered, "mapping": {content_id: new_id}}
            )
        ).status_code == 422


async def test_finalize_is_atomic_and_deduplicates_across_instances(database, settings):
    import asyncio

    from mockazure.db.engine import close_database, create_database

    async with graph_client(database, settings) as (_, tenant, *_):
        ids = []
        for _ in range(2):
            async with database.sessions.begin() as session:
                identifier = await begin_upload(
                    session, tenant, 3, hashlib.sha256(b"abc").hexdigest(), "text/plain"
                )
                await put_chunk(session, tenant, identifier, 0, b"abc")
                ids.append(identifier)
        second = create_database(settings)

        async def finish(db, identifier):
            async with db.sessions.begin() as session:
                return await finalize_upload(session, tenant, identifier)

        try:
            first, other = await asyncio.gather(finish(database, ids[0]), finish(second, ids[1]))
            assert first.id == other.id
            assert (await finish(database, ids[1])).id == first.id
        finally:
            await close_database(second)


async def test_content_upgrade_preserves_exchange_and_blocks_lossy_downgrade(settings):
    from alembic import command as migrations
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError

    from mockazure.db.migrations import configuration, migrate

    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    tenant = uuid4()
    try:
        with engine.begin() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            migrations.upgrade(config, "041")
            connection.execute(
                text(
                    "INSERT INTO tenants (id,slug,display_name,domain,licences,revision) "
                    "VALUES (:id,'binary','Binary','binary.test','[]',1)"
                ),
                {"id": tenant},
            )
            connection.execute(
                text(
                    "INSERT INTO mail_exchange_configuration (tenant_id,organization) "
                    "VALUES (:id, jsonb_build_object('AuditDisabled',true))"
                ),
                {"id": tenant},
            )
        migrate(settings.database_url.get_secret_value())
        with engine.begin() as connection:
            assert (
                connection.scalar(
                    text("SELECT organization->>'AuditDisabled' FROM mail_exchange_configuration")
                )
                == "true"
            )
            connection.execute(
                text(
                    "INSERT INTO content_objects (tenant_id,id,size,sha256,media_type) "
                    "VALUES (:tenant,:id,0,:sha,'text/plain')"
                ),
                {"tenant": tenant, "id": uuid4(), "sha": hashlib.sha256(b"").hexdigest()},
            )
        with pytest.raises(DBAPIError, match="Export and reset content"):
            with engine.begin() as connection:
                config = configuration()
                config.attributes["connection"] = connection
                migrations.downgrade(config, "041")
    finally:
        engine.dispose()


async def test_256_mib_content_uses_bounded_chunks(database, settings):
    import os
    import tracemalloc

    if os.environ.get("MOCKAZURE_LARGE_CONTENT_TEST") != "1":
        pytest.skip("Explicit 256 MiB scale qualification")
    async with graph_client(database, settings) as (_, tenant, *_):
        data = b"x" * CHUNK
        digest = hashlib.sha256()
        for _ in range(256):
            digest.update(data)
        tracemalloc.start()
        try:
            async with database.sessions.begin() as session:
                identifier = await begin_upload(
                    session, tenant, 256 * CHUNK, digest.hexdigest(), "application/octet-stream"
                )
                for ordinal in range(256):
                    await put_chunk(session, tenant, identifier, ordinal, data)
                ref = await finalize_upload(session, tenant, identifier)
            observed, size = hashlib.sha256(), 0
            async with database.sessions() as session:
                async for chunk in stream_content(session, tenant, ref.id):
                    assert len(chunk) <= CHUNK
                    observed.update(chunk)
                    size += len(chunk)
            _, peak = tracemalloc.get_traced_memory()
            assert observed.hexdigest() == digest.hexdigest()
            assert size == 256 * CHUNK
            assert peak < 24 * CHUNK, f"Python heap peak {peak} exceeds bounded streaming allowance"
            print({"bytes": size, "sha256": observed.hexdigest(), "python_peak_bytes": peak})
        finally:
            tracemalloc.stop()


async def test_cleanup_skips_inflight_reference_and_private_routes_require_admin(
    database, settings
):
    from mockazure.db.engine import close_database, create_database

    async with graph_client(database, settings) as (client, tenant, *_):
        assert (
            await client.get(
                f"/_mock/tenants/{tenant}/content/manifest",
                headers={"Authorization": "Bearer invalid"},
            )
        ).status_code == 401
        ref = await upload(database, tenant, b"pinned")
        other = create_database(settings)
        try:
            async with database.sessions.begin() as writer:
                await pin_content(writer, tenant, ref.id, "message-version", "v1")
                async with other.sessions.begin() as cleaner:
                    result = await prune_content(
                        cleaner, tenant, datetime.now(UTC) + timedelta(days=2), 10
                    )
                    assert result["deleted"] == 0
            async with other.sessions.begin() as cleaner:
                result = await prune_content(
                    cleaner, tenant, datetime.now(UTC) + timedelta(days=2), 10
                )
                assert result["deleted"] == 0
        finally:
            await close_database(other)


async def test_empty_object_chunk_bounds_and_configured_limit(database, settings):
    async with graph_client(database, settings) as (client, tenant, *_):
        empty = await upload(database, tenant, b"")
        async with database.sessions() as session:
            assert [part async for part in stream_content(session, tenant, empty.id)] == []
        base = f"/_mock/tenants/{tenant}/content"
        rejected = await client.post(
            base + "/uploads",
            json={
                "size": 256 * CHUNK + 1,
                "sha256": "0" * 64,
                "media_type": "application/octet-stream",
            },
        )
        assert rejected.status_code == 422, rejected.text
        created = await client.post(
            base + "/uploads",
            json={"size": CHUNK + 1, "sha256": "0" * 64, "media_type": "application/octet-stream"},
        )
        identifier = created.json()["uploadId"]
        oversized = await client.put(
            base + f"/uploads/{identifier}/chunks/0", content=b"x" * (CHUNK + 1)
        )
        assert oversized.status_code == 413, oversized.text
        wrong_tail = await client.put(base + f"/uploads/{identifier}/chunks/1", content=b"xx")
        assert wrong_tail.status_code == 422, wrong_tail.text
        assert (await client.post(base + f"/uploads/{identifier}/complete")).status_code == 422


async def test_shared_maintenance_prunes_content_within_budget(database, settings):
    from mockazure.content.models import ContentObject

    async with graph_client(database, settings) as (client, tenant, *_):
        first = await upload(database, tenant, b"first")
        second = await upload(database, tenant, b"second")
        async with database.sessions.begin() as session:
            for ref in (first, second):
                row = await session.get(ContentObject, (tenant, ref.id))
                row.created_at = datetime.now(UTC) - timedelta(days=2)
        result = await client.post(
            f"/_mock/tenants/{tenant}/maintenance/cleanup", json={"max_rows": 1}
        )
        assert result.status_code == 200, result.text
        assert result.json()["deleted"] == 1
        assert result.json()["content"]["deleted"] == 1
        assert result.json()["remaining"] is True

from datetime import UTC, datetime, timedelta

import httpx

from mockazure.admin.app import create_app
from mockazure.db.engine import close_database, create_database
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_mail_content import MIME, seed_mail


async def collect(client, url, headers):
    values = []
    for _ in range(20):
        response = await client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        values.extend(body["value"])
        if "@odata.nextLink" not in body:
            return values, body.get("@odata.deltaLink")
        url = body["@odata.nextLink"]
    raise AssertionError("Unbounded paging")


async def test_move_id_preferences(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.ReadWrite"})
        root = f"/v1.0/users/{user}/messages"
        immutable = {**headers, "Prefer": 'IdType="ImmutableId"'}
        original = (await client.get(root + "/m1", headers=immutable)).json()["id"]
        moved = await client.post(
            root + "/m1/move", headers=headers, json={"destinationId": "archive"}
        )
        assert moved.status_code == 201, moved.text
        assert moved.json()["id"] != "m1"
        assert moved.json()["parentFolderId"] == "archive-local"
        assert (await client.get(root + "/m1", headers=headers)).status_code == 404
        assert (await client.get(root + "/" + original, headers=immutable)).json()["id"] == original
        moved_back = await client.post(
            root + "/" + original + "/move", headers=immutable, json={"destinationId": "inbox"}
        )
        assert moved_back.status_code == 201, moved_back.text
        assert moved_back.json()["id"] == original
        assert (
            await client.get(root + "/" + original + "/$value", headers=headers)
        ).content == MIME


async def test_copy_allocates_independent_identity(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        headers = {
            **await headers_for(database, settings, tenant, app, None, {"Mail.ReadWrite"}),
            "Prefer": 'IdType="ImmutableId"',
        }
        root = f"/v1.0/users/{user}/messages"
        original = (await client.get(root + "/m1", headers=headers)).json()["id"]
        copied = await client.post(
            root + "/m1/copy", headers=headers, json={"destinationId": "archive"}
        )
        assert copied.status_code == 201, copied.text
        identifier = copied.json()["id"]
        assert identifier != original
        changed = await client.patch(
            root + "/" + identifier,
            headers=headers,
            json={"isRead": True, "categories": ["Reviewed"]},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["categories"] == ["Reviewed"]
        assert (await client.get(root + "/m1", headers=headers)).json()["isRead"] is False
        assert (await client.delete(root + "/" + identifier, headers=headers)).status_code == 204
        assert (await client.get(root + "/m1/$value", headers=headers)).content == MIME
        assert (
            await client.patch(root + "/m1", headers=headers, json={"subject": "Unsupported"})
        ).status_code == 400


async def test_delta_move_and_delete(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.ReadWrite"})
        root = f"/v1.0/users/{user}"
        initial, source = await collect(
            client, root + "/mailFolders/inbox/messages/delta?$top=1", headers
        )
        assert {v["id"] for v in initial} == {"m1", "m2"}
        _, destination = await collect(
            client, root + "/mailFolders/archive/messages/delta", headers
        )
        assert source and destination
        moved = await client.post(
            root + "/messages/m1/move", headers=headers, json={"destinationId": "archive"}
        )
        assert moved.status_code == 201, moved.text
        assert (await client.delete(root + "/messages/m2", headers=headers)).status_code == 204
        removed, source = await collect(client, source, headers)
        assert {v["id"] for v in removed} == {"m1", "m2"}
        assert all(v["@removed"]["reason"] == "deleted" for v in removed)
        added, destination = await collect(client, destination, headers)
        assert [v["id"] for v in added] == [moved.json()["id"]]
        unchanged, _ = await collect(client, source, headers)
        assert unchanged == []


async def test_delta_restart_two_instances(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.ReadWrite"})
        root = f"/v1.0/users/{user}"
        first = await client.get(
            root + "/mailFolders/inbox/messages/delta()?$top=1", headers=headers
        )
        assert first.status_code == 200, first.text
        await client.post(
            root + "/messages/m1/move", headers=headers, json={"destinationId": "archive"}
        )
        replica_db = create_database(settings)
        replica = create_app(settings, database=replica_db)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=replica), base_url="https://graph.mockazure.test"
            ) as other:
                rest, delta = await collect(other, first.json()["@odata.nextLink"], headers)
                assert {v["id"] for v in first.json()["value"] + rest} == {"m1", "m2"}
                await replica_db.engine.dispose()
                changes, _ = await collect(other, delta, headers)
                assert changes == [{"id": "m1", "@removed": {"reason": "deleted"}}]
        finally:
            await close_database(replica_db)


async def test_delta_reauthorizes(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
        _, delta = await collect(
            client, f"/v1.0/users/{user}/mailFolders/inbox/messages/delta", headers
        )
        denied = await headers_for(database, settings, tenant, app, None, {"User.Read.All"})
        assert (await client.get(delta, headers=denied)).status_code == 403
        assert (await client.get(delta + "x", headers=headers)).status_code == 400
        assert (
            await client.get(delta.replace("/inbox/", "/archive-local/"), headers=headers)
        ).status_code == 400
        application = client._transport.app
        application.state.clock = lambda: datetime.now(UTC) + timedelta(hours=2)
        assert (await client.get(delta, headers=headers)).status_code == 410


async def test_selection_and_received_filter_continuation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.ReadWrite"})
        root = f"/v1.0/users/{user}"
        values, delta = await collect(
            client,
            root
            + "/mailFolders/inbox/messages/delta?$top=1&$select=subject"
            + "&$filter=receivedDateTime ge 2026-10-07T10:30:00Z",
            headers,
        )
        assert values == [{"id": "m2", "subject": "Second"}]
        assert (
            await client.patch(root + "/messages/m1", headers=headers, json={"isRead": True})
        ).status_code == 200
        values, delta = await collect(client, delta, headers)
        # Folder synchronization emits read-state changes even outside the received filter.
        assert values == [{"id": "m1", "subject": "Evidence"}]
        await client.delete(root + "/messages/m1", headers=headers)
        values, _ = await collect(client, delta, headers)
        assert values == [{"id": "m1", "@removed": {"reason": "deleted"}}]


async def test_message_native_audit_and_strict_sdk_patch(database, settings):
    from tests.integration.test_evidence_directory import drain
    from tests.integration.test_mail_audit import mail_sources, observations

    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        await client.patch(f"/_mock/tenants/{tenant}/evidence/settings", json={"delay_seconds": 0})
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.ReadWrite"})
        root = f"/v1.0/users/{user}/messages/m1"
        updated = await client.patch(
            root, headers=headers, json={"@odata.type": "#microsoft.graph.message", "isRead": True}
        )
        assert updated.status_code == 200, updated.text
        duplicate = await client.patch(
            root,
            headers={**headers, "Content-Type": "application/json"},
            content=b'{"isRead":false,"isRead":true}',
        )
        assert duplicate.status_code == 400
        # Idempotent property update emits no second native fact.
        await client.patch(root, headers=headers, json={"isRead": True})
        assert len(await mail_sources(database, tenant)) == 1
        await drain(database)
        audits = await observations(database, tenant)
        assert len(audits) == 1
        assert audits[0].payload["operation"] == "Update"
        assert audits[0].payload["auditLogRecordType"] == "ExchangeItem"
        assert audits[0].payload["auditData"]["dynamicProperties"]["RecordType"] == 2


async def test_cleanup_preserves_active_delta_and_releases_expired_blob_pins(database, settings):
    from sqlalchemy import func, select

    from mockazure.content.models import ContentReference
    from mockazure.mail.message_cleanup import cleanup_mail_content
    from mockazure.mail.message_models import MailMessageVersion

    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.ReadWrite"})
        root = f"/v1.0/users/{user}"
        _, delta = await collect(client, root + "/mailFolders/inbox/messages/delta", headers)
        await client.delete(root + "/messages/m1", headers=headers)
        async with database.sessions.begin() as session:
            kept = await cleanup_mail_content(session, tenant, datetime.now(UTC), 100)
            assert kept["versions"] == 0
        changes, _ = await collect(client, delta, headers)
        assert changes == [{"id": "m1", "@removed": {"reason": "deleted"}}]
        async with database.sessions.begin() as session:
            cleaned = await cleanup_mail_content(
                session, tenant, datetime.now(UTC) + timedelta(hours=2), 100
            )
            assert cleaned["versions"] == 2
            assert cleaned["expired_handles"] >= 2
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ContentReference)
                    .where(ContentReference.tenant_id == tenant)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(MailMessageVersion)
                    .where(MailMessageVersion.tenant_id == tenant)
                )
                == 1
            )


async def test_mail_batch_prefer_and_shared_read_budget(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
        settings.graph_directory_request_limit = 2
        response = await client.post(
            "/v1.0/$batch",
            headers=headers,
            json={
                "requests": [
                    {
                        "id": str(i),
                        "method": "GET",
                        "url": f"/users/{user}/messages/m1",
                        "headers": {"Prefer": 'IdType="ImmutableId"'},
                    }
                    for i in range(3)
                ]
            },
        )
        assert response.status_code == 200, response.text
        rows = response.json()["responses"]
        assert [v["status"] for v in rows] == [200, 200, 429]
        assert rows[0]["body"]["id"].startswith("AAIk")
        assert int(rows[2]["headers"]["retry-after"]) >= 1


async def test_raw_response_pins_bytes_before_headers_are_sent(database, settings):
    from starlette.requests import Request

    from mockazure.content.repository import prune_content
    from mockazure.mail.message_cleanup import cleanup_mail_content

    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.ReadWrite"})
        application = client._transport.app
        from mockazure.providers.graph.mail_content_routes import router

        route = next(
            route
            for route in router.routes
            if getattr(route, "path", "") == "/v1.0/users/{user_id}/messages/{message_id}/$value"
        )
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "scheme": "https",
                "server": ("localhost", 8443),
                "path": f"/v1.0/users/{user}/messages/m1/$value",
                "query_string": b"",
                "app": application,
                "headers": [
                    (key.lower().encode(), value.encode()) for key, value in headers.items()
                ],
                "path_params": {"user_id": str(user), "message_id": "m1"},
            }
        )
        response = await route.endpoint(request)
        try:
            assert (
                await client.delete(f"/v1.0/users/{user}/messages/m1", headers=headers)
            ).status_code == 204
            async with database.sessions.begin() as session:
                # The MIME object must already be locked; update only its unrelated attachment.
                await cleanup_mail_content(session, tenant, datetime.now(UTC), 100)
                cleaned = await prune_content(
                    session, tenant, datetime.now(UTC) + timedelta(days=2), 100
                )
                assert cleaned["deleted"] == 1
            raw = b"".join([part async for part in response.body_iterator])
            assert raw == MIME
        finally:
            if response.background:
                await response.background()


async def test_raw_content_works_with_one_database_connection(database, settings):
    import asyncio

    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
        limited = create_database(
            settings.model_copy(update={"pool_size": 1, "pool_max_overflow": 0})
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(settings, limited)),
                base_url="https://localhost:8443",
            ) as other:
                response = await asyncio.wait_for(
                    other.get(f"/v1.0/users/{user}/messages/m1/$value", headers=headers), timeout=3
                )
                assert response.status_code == 200, response.text
                assert response.content == MIME
        finally:
            await close_database(limited)

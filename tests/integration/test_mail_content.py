from uuid import uuid4

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_content_storage import upload
from tests.integration.test_mailbox_state import provision

MIME = b"Subject: Raw evidence\r\nX-Source: local\r\n\r\nbody\x00bytes"


async def seed_mail(client, database, tenant, user, *, hidden=False):
    await provision(
        client,
        tenant,
        user,
        folders=[
            {"id": "inbox", "name": "Inbox", "path": "/Inbox", "item_count": 99, "size_bytes": 9999}
        ],
    )
    raw = await upload(database, tenant, MIME)
    attachment = await upload(database, tenant, b"attachment\x00bytes")
    batch = {
        "mailbox_user_id": str(user),
        "mode": "content-backed",
        "expected_revision": 0,
        "folders": [
            {"id": "inbox", "displayName": "Inbox", "well_known_name": "inbox"},
            {"id": "archive-local", "displayName": "Archive", "well_known_name": "archive"},
            {"id": "hidden", "displayName": "Hidden", "isHidden": True},
        ],
        "messages": [
            {
                "id": "m1",
                "folder_id": "inbox",
                "mime_content_id": str(raw.id),
                "payload": {
                    "subject": "Evidence",
                    "receivedDateTime": "2026-10-07T10:00:00Z",
                    "isRead": False,
                    "body": {"contentType": "html", "content": "<p>Secret body</p>"},
                    "bodyPreview": "Secret body",
                    "internetMessageHeaders": [{"name": "X-Source", "value": "local"}],
                },
            },
            {
                "id": "m2",
                "folder_id": "inbox",
                "payload": {
                    "subject": "Second",
                    "receivedDateTime": "2026-10-07T11:00:00Z",
                    "isRead": True,
                },
            },
        ],
        "attachments": [
            {
                "message_id": "m1",
                "id": "a1",
                "kind": "fileAttachment",
                "content_id": str(attachment.id),
                "payload": {
                    "name": "evidence.bin",
                    "contentType": "application/octet-stream",
                    "isInline": False,
                },
            },
            {
                "message_id": "m1",
                "id": "a2",
                "kind": "itemAttachment",
                "content_id": str(raw.id),
                "item": {"@odata.type": "#microsoft.graph.message", "subject": "Nested"},
                "payload": {"name": "nested.eml"},
            },
            {
                "message_id": "m1",
                "id": "a3",
                "kind": "referenceAttachment",
                "payload": {"name": "External reference"},
            },
        ],
        "mailbox_settings": {
            "timeZone": "UTC",
            "dateFormat": "yyyy-MM-dd",
            "language": {"locale": "en-US", "displayName": "English"},
        },
    }
    response = await client.post(f"/_mock/tenants/{tenant}/mail/content/import", json=batch)
    assert response.status_code == 200, response.text
    return batch


async def test_legacy_statistics_mode_preserved(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(
            client,
            tenant,
            user,
            folders=[
                {
                    "id": "inbox",
                    "name": "Inbox",
                    "path": "/Inbox",
                    "item_count": 99,
                    "size_bytes": 9999,
                }
            ],
        )
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
        messages = await client.get(f"/v1.0/users/{user}/messages", headers=headers)
        assert messages.status_code == 200, messages.text
        assert messages.json()["value"] == []
        folders = await client.get(f"/v1.0/users/{user}/mailFolders", headers=headers)
        assert folders.json()["value"][0]["totalItemCount"] == 99
        assert (
            await client.get(f"/beta/users/{user}/messages", headers=headers)
        ).status_code == 501


async def test_content_mode_statistics(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
        folder = await client.get(f"/v1.0/users/{user}/mailFolders/inbox", headers=headers)
        assert folder.status_code == 200, folder.text
        assert folder.json()["totalItemCount"] == 2
        assert folder.json()["unreadItemCount"] == 1
        # Compiled EXO command has a separate route; compare the shared projection directly.
        from mockazure.mail.mailboxes import mailbox_statistics
        from mockazure.mail.repository import resolve_mailbox

        async with database.sessions() as session:
            value = await mailbox_statistics(
                session, await resolve_mailbox(session, tenant, str(user))
            )
        assert value["ItemCount"] == 2
        assert value["TotalItemSizeBytes"] == len(MIME)


async def test_message_body_permissions(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        basic = await headers_for(database, settings, tenant, app, None, {"Mail.ReadBasic.All"})
        response = await client.get(
            f"/v1.0/users/{user}/messages/m1?$select=id,subject,body,bodyPreview", headers=basic
        )
        assert response.status_code == 200, response.text
        assert response.json()["subject"] == "Evidence"
        assert "body" not in response.json() and "bodyPreview" not in response.json()
        for suffix in ("/$value", "/attachments", "/attachments/a1/$value"):
            assert (
                await client.get(f"/v1.0/users/{user}/messages/m1" + suffix, headers=basic)
            ).status_code == 403
        full = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
        response = await client.get(
            f"/v1.0/users/{user}/messages/m1",
            headers={**full, "Prefer": 'outlook.body-content-type="text"'},
        )
        assert response.json()["body"] == {"contentType": "text", "content": "Secret body"}
        assert "internetMessageHeaders" not in response.json()
        selected = await client.get(
            f"/v1.0/users/{user}/messages/m1?$select=internetMessageHeaders", headers=full
        )
        assert selected.json()["internetMessageHeaders"] == [{"name": "X-Source", "value": "local"}]


async def test_folder_aliases_and_hidden_paging(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        delegated = await headers_for(
            database, settings, tenant, app, user, {"Mail.Read"}, kind="delegated"
        )
        first = await client.get(
            "/v1.0/me/mailFolders?$top=1&includeHiddenFolders=true", headers=delegated
        )
        assert first.status_code == 200, first.text
        values = first.json()["value"]
        while link := first.json().get("@odata.nextLink"):
            first = await client.get(link, headers=delegated)
            assert first.status_code == 200, first.text
            values.extend(first.json()["value"])
        assert {row["id"] for row in values} == {"inbox", "archive-local", "hidden"}
        ordinary = await client.get("/v1.0/me/mailFolders", headers=delegated)
        assert len(ordinary.json()["value"]) == 2
        assert (await client.get("/v1.0/me/mailFolders/archive", headers=delegated)).json()[
            "id"
        ] == "archive-local"


async def test_attachment_types_and_raw_mime(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
        base = f"/v1.0/users/{user}/messages/m1"
        assert (await client.get(base + "/$value", headers=headers)).content == MIME
        attachments = await client.get(base + "/attachments", headers=headers)
        assert attachments.status_code == 200, attachments.text
        assert {row["@odata.type"] for row in attachments.json()["value"]} == {
            "#microsoft.graph.fileAttachment",
            "#microsoft.graph.itemAttachment",
            "#microsoft.graph.referenceAttachment",
        }
        assert (
            await client.get(base + "/attachments/a1/$value", headers=headers)
        ).content == b"attachment\x00bytes"
        assert (await client.get(base + "/attachments/a2/$value", headers=headers)).content == MIME
        assert (
            await client.get(base + "/attachments/a3/$value", headers=headers)
        ).status_code == 405
        missing = await client.get(
            f"/v1.0/users/{user}/messages/m2/attachments/a1/$value", headers=headers
        )
        assert missing.status_code == 404


async def test_mailbox_settings_permission(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        read = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
        assert (
            await client.get(f"/v1.0/users/{user}/mailboxSettings", headers=read)
        ).status_code == 403
        permitted = await headers_for(
            database, settings, tenant, app, None, {"MailboxSettings.Read"}
        )
        response = await client.get(f"/v1.0/users/{user}/mailboxSettings", headers=permitted)
        assert response.status_code == 200, response.text
        assert response.json()["timeZone"] == "UTC"


async def test_invalid_batch_is_atomic(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        batch = await seed_mail(client, database, tenant, user)
        batch["expected_revision"] = 1
        batch["messages"][0]["payload"]["subject"] = "Must roll back"
        batch["messages"][1]["mime_content_id"] = str(uuid4())
        failed = await client.post(f"/_mock/tenants/{tenant}/mail/content/import", json=batch)
        assert failed.status_code in {404, 422}, failed.text
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
        message = await client.get(f"/v1.0/users/{user}/messages/m1", headers=headers)
        assert message.json()["subject"] == "Evidence"
        batch["expected_revision"] = 0
        assert (
            await client.post(f"/_mock/tenants/{tenant}/mail/content/import", json=batch)
        ).status_code == 409


async def test_mail_content_export_reset_restore_and_provisioning_guard(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        path = f"/_mock/tenants/{tenant}/mail/content/{user}"
        exported = await client.get(path + "/export")
        assert exported.status_code == 200, exported.text
        protected = await client.put(
            f"/_mock/tenants/{tenant}/mail/mailboxes/{user}",
            json={"primary_smtp": "reader@contoso.test", "alias": "reader", "folders": []},
        )
        assert protected.status_code == 409, protected.text
        changed = await client.put(
            f"/_mock/tenants/{tenant}/mail/mailboxes/{user}",
            json={"primary_smtp": "reader@contoso.test", "alias": "updated"},
        )
        assert changed.status_code == 200, changed.text
        assert (
            await client.post(path + "/reset", json={"expected_revision": 0})
        ).status_code == 409
        assert (
            await client.post(path + "/reset", json={"expected_revision": 1})
        ).status_code == 200
        batch = exported.json()
        batch["expected_revision"] = 0
        restored = await client.post(f"/_mock/tenants/{tenant}/mail/content/import", json=batch)
        assert restored.status_code == 200, restored.text
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
        assert (
            await client.get(f"/v1.0/users/{user}/messages/m1/$value", headers=headers)
        ).content == MIME


async def test_import_rejects_attachment_tombstone_conflict_and_duplicate_immutable_id(
    database, settings
):
    async with graph_client(database, settings) as (client, tenant, app, user):
        batch = await seed_mail(client, database, tenant, user)
        batch["expected_revision"] = 1
        batch["messages"] = []
        batch["tombstones"] = ["m1"]
        assert (
            await client.post(f"/_mock/tenants/{tenant}/mail/content/import", json=batch)
        ).status_code == 422
        duplicate = str(uuid4())
        batch["tombstones"] = []
        batch["attachments"] = []
        batch["messages"] = [
            {"id": "m3", "immutable_id": duplicate, "folder_id": "inbox", "payload": {}},
            {"id": "m4", "immutable_id": duplicate, "folder_id": "inbox", "payload": {}},
        ]
        assert (
            await client.post(f"/_mock/tenants/{tenant}/mail/content/import", json=batch)
        ).status_code == 422


async def test_mail_export_streams_typed_records(database, settings):
    import json

    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        response = await client.get(
            f"/_mock/tenants/{tenant}/mail/content/{user}/export?format=ndjson"
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/x-ndjson")
        records = [json.loads(line) for line in response.text.splitlines()]
        assert records[0]["type"] == "mailbox"
        assert sum(row["type"] == "message" for row in records) == 2
        assert sum(row["type"] == "attachment" for row in records) == 3


async def test_archive_statistics_survive_transition_and_mailbox_metadata_update(
    database, settings
):
    from mockazure.mail.mailboxes import mailbox_statistics
    from mockazure.mail.repository import resolve_mailbox

    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(
            client,
            tenant,
            user,
            archive_enabled=True,
            folders=[
                {
                    "id": "inbox",
                    "name": "Inbox",
                    "path": "/Inbox",
                    "item_count": 99,
                    "size_bytes": 100,
                },
                {
                    "id": "archive-evidence",
                    "name": "Archive Inbox",
                    "path": "/Inbox",
                    "archive": True,
                    "item_count": 33,
                    "size_bytes": 333,
                },
            ],
        )
        batch = {
            "mailbox_user_id": str(user),
            "mode": "content-backed",
            "expected_revision": 0,
            "messages": [{"id": "primary", "folder_id": "inbox", "payload": {"subject": "Stored"}}],
        }
        assert (
            await client.post(f"/_mock/tenants/{tenant}/mail/content/import", json=batch)
        ).status_code == 200
        update = await client.put(
            f"/_mock/tenants/{tenant}/mail/mailboxes/{user}",
            json={"primary_smtp": "reader@contoso.test", "alias": "updated"},
        )
        assert update.status_code == 200, update.text
        assert update.json()["ArchiveStatus"] == "Active"
        async with database.sessions() as session:
            box = await resolve_mailbox(session, tenant, str(user))
            assert (await mailbox_statistics(session, box, archive=True))["ItemCount"] == 33
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
        assert (
            await client.get(f"/v1.0/users/{user}/mailFolders/archive-evidence", headers=headers)
        ).status_code == 404


async def test_mail_read_permission_checked_before_unknown_mailbox_lookup(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(database, settings, tenant, app, None, {"User.Read.All"})
        result = await client.get(f"/v1.0/users/{uuid4()}/messages", headers=headers)
        assert result.status_code == 403, result.text


async def test_mail_content_upgrade_preserves_legacy_statistics_and_blocks_lossy_downgrade(
    database, settings
):
    import pytest
    from alembic import command as migrations
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError

    from mockazure.db.migrations import configuration, migrate
    from mockazure.mail.mailboxes import mailbox_statistics
    from mockazure.mail.repository import resolve_mailbox

    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(
            client,
            tenant,
            user,
            folders=[
                {
                    "id": "inbox",
                    "name": "Inbox",
                    "path": "/Inbox",
                    "item_count": 99,
                    "size_bytes": 999,
                }
            ],
        )
        engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
        try:
            with engine.begin() as connection:
                config = configuration()
                config.attributes["connection"] = connection
                migrations.downgrade(config, "042")
            migrate(settings.database_url.get_secret_value())
            async with database.sessions() as session:
                box = await resolve_mailbox(session, tenant, str(user))
                assert (await mailbox_statistics(session, box))["ItemCount"] == 99
            batch = {"mailbox_user_id": str(user), "mode": "content-backed", "expected_revision": 0}
            result = await client.post(f"/_mock/tenants/{tenant}/mail/content/import", json=batch)
            assert result.status_code == 200, result.text
            with pytest.raises(DBAPIError, match="Export and clear mailbox content"):
                with engine.begin() as connection:
                    config = configuration()
                    config.attributes["connection"] = connection
                    migrations.downgrade(config, "042")
        finally:
            engine.dispose()

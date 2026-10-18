"""Regression triggers from the fresh mail/content branch review."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from mockazure.content.models import ContentObject
from mockazure.content.repository import finalize_upload, prune_content, stream_content
from mockazure.mail.messages import immutable_identifier
from mockazure.mail.models import MailboxFolder
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_content_storage import upload
from tests.integration.test_mail_content import seed_mail


async def test_reserved_normal_message_id_rejected_atomically(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        batch = await seed_mail(client, database, tenant, user)
        key = uuid4()
        batch["expected_revision"] = 1
        batch["messages"] = [
            {"id": immutable_identifier(key), "folder_id": "inbox", "payload": {"subject": "A"}},
            {
                "id": "target",
                "immutable_id": str(key),
                "folder_id": "inbox",
                "payload": {"subject": "B"},
            },
        ]
        batch["attachments"] = []
        result = await client.post(f"/_mock/tenants/{tenant}/mail/content/import", json=batch)
        assert result.status_code == 422, result.text
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
        result = await client.get(f"/v1.0/users/{user}/messages", headers=headers)
        assert {row["id"] for row in result.json()["value"]} == {"m1", "m2"}


@pytest.mark.parametrize("content_type", ["text", "html"])
async def test_nullable_body_survives_all_projections(database, settings, content_type):
    async with graph_client(database, settings) as (client, tenant, app, user):
        batch = await seed_mail(client, database, tenant, user)
        batch["expected_revision"] = 1
        batch["messages"][0]["payload"]["body"] = {"contentType": content_type, "content": None}
        result = await client.post(f"/_mock/tenants/{tenant}/mail/content/import", json=batch)
        assert result.status_code == 200, result.text
        for permission in ("Mail.Read", "Mail.ReadBasic.All"):
            headers = await headers_for(database, settings, tenant, app, None, {permission})
            for preference in ("html", "text"):
                for suffix in ("", "?$select=subject"):
                    result = await client.get(
                        f"/v1.0/users/{user}/messages" + suffix,
                        headers={**headers, "Prefer": f'outlook.body-content-type="{preference}"'},
                    )
                    assert result.status_code == 200, result.text
                    first = next(row for row in result.json()["value"] if row["id"] == "m1")
                    if permission == "Mail.Read" and not suffix:
                        assert first["body"]["content"] is None
                    else:
                        assert "body" not in first


async def test_fresh_completion_renews_unreferenced_blob_grace(database, settings):
    async with graph_client(database, settings) as (_, tenant, *_):
        ref = await upload(database, tenant, b"dedup grace")
        async with database.sessions.begin() as session:
            row = await session.get(ContentObject, (tenant, ref.id))
            row.created_at = datetime.now(UTC) - timedelta(days=3)
        duplicate = await upload(database, tenant, b"dedup grace")
        assert duplicate.id == ref.id
        async with database.sessions.begin() as session:
            await prune_content(session, tenant, datetime.now(UTC), 100)
            assert (
                b"".join([part async for part in stream_content(session, tenant, ref.id)])
                == b"dedup grace"
            )
        # Retrying either receipt must renew the same canonical object's grace.
        async with database.sessions() as session:
            aliases = list(
                await session.scalars(
                    select(ContentObject.id).where(
                        ContentObject.tenant_id == tenant, ContentObject.state == "alias"
                    )
                )
            )
        for receipt in [ref.id, *aliases]:
            async with database.sessions.begin() as session:
                row = await session.get(ContentObject, (tenant, ref.id))
                row.created_at = datetime.now(UTC) - timedelta(days=3)
            async with database.sessions.begin() as session:
                assert (await finalize_upload(session, tenant, receipt)).id == ref.id
            async with database.sessions.begin() as session:
                await prune_content(session, tenant, datetime.now(UTC), 100)
                assert await session.get(ContentObject, (tenant, ref.id)) is not None


async def test_folder_alias_swap_is_atomic(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        batch = await seed_mail(client, database, tenant, user)
        batch.update(expected_revision=1, messages=[], attachments=[])
        batch["folders"][0]["well_known_name"] = "archive"
        batch["folders"][1]["well_known_name"] = "inbox"
        result = await client.post(f"/_mock/tenants/{tenant}/mail/content/import", json=batch)
        assert result.status_code == 200, result.text
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
        result = await client.get(f"/v1.0/users/{user}/mailFolders/archive", headers=headers)
        assert result.json()["id"] == "inbox"


async def test_attachment_page_has_aggregate_byte_budget(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_mail(client, database, tenant, user)
        blob = await upload(database, tenant, b"x" * (4 * 1024 * 1024))
        result = await client.post(
            f"/_mock/tenants/{tenant}/mail/content/import",
            json={
                "mailbox_user_id": str(user),
                "mode": "content-backed",
                "expected_revision": 1,
                "attachments": [
                    {
                        "message_id": "m2",
                        "id": f"large-{i}",
                        "kind": "fileAttachment",
                        "content_id": str(blob.id),
                        "payload": {"name": "large.bin"},
                    }
                    for i in range(3)
                ],
            },
        )
        assert result.status_code == 200, result.text
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
        link = f"/v1.0/users/{user}/messages/m2/attachments?$top=1000"
        ids, pages = [], 0
        while link:
            result = await client.get(link, headers=headers)
            assert result.status_code == 200, result.text[:200]
            assert len(result.content) <= 8 * 1024 * 1024
            ids.extend(row["id"] for row in result.json()["value"])
            link = result.json().get("@odata.nextLink")
            pages += 1
            assert pages <= 3
        assert ids == ["large-0", "large-1", "large-2"]
        assert pages == 3


async def test_seven_digit_received_time_filters_and_delta(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        batch = await seed_mail(client, database, tenant, user)
        batch.update(expected_revision=1, attachments=[])
        batch["messages"] = [
            {
                "id": f"precise-{i}",
                "folder_id": "inbox",
                "payload": {"receivedDateTime": f"2026-10-07T10:30:00.123456{i}Z"},
            }
            for i in (6, 7, 8)
        ]
        result = await client.post(f"/_mock/tenants/{tenant}/mail/content/import", json=batch)
        assert result.status_code == 200, result.text
        headers = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
        base = f"/v1.0/users/{user}/mailFolders/inbox/messages"
        for operator, expected in (
            ("eq", {"precise-7"}),
            ("ge", {"precise-7", "precise-8", "m2"}),
            ("gt", {"precise-8", "m2"}),
        ):
            result = await client.get(
                base,
                headers=headers,
                params={
                    "$filter": f"receivedDateTime {operator} 2026-10-07T12:30:00.1234567+02:00"
                },
            )
            assert result.status_code == 200, result.text
            assert {row["id"] for row in result.json()["value"]} == expected
        result = await client.get(
            base + "/delta",
            headers=headers,
            params={"$filter": "receivedDateTime gt 2026-10-07T10:30:00.1234567Z"},
        )
        assert {row["id"] for row in result.json()["value"]} == {"precise-8", "m2"}
        delta = result.json()["@odata.deltaLink"]
        batch["expected_revision"] = 2
        batch["messages"] = [
            {
                "id": "new-boundary",
                "folder_id": "inbox",
                "payload": {"receivedDateTime": "2026-10-07T10:30:00.1234567Z"},
            },
            {
                "id": "new-above",
                "folder_id": "inbox",
                "payload": {"receivedDateTime": "2026-10-07T10:30:00.1234568Z"},
            },
        ]
        assert (
            await client.post(f"/_mock/tenants/{tenant}/mail/content/import", json=batch)
        ).status_code == 200
        result = await client.get(delta, headers=headers)
        assert result.status_code == 200, result.text
        assert {row["id"] for row in result.json()["value"]} == {"new-above"}


async def test_folder_paths_follow_renames_and_reparenting(database, settings):
    async with graph_client(database, settings) as (client, tenant, _, user):
        batch = await seed_mail(client, database, tenant, user)
        batch.update(
            expected_revision=1,
            messages=[],
            attachments=[],
            folders=[
                {"id": "child", "displayName": "Child", "parentFolderId": "inbox"},
                {"id": "grandchild", "displayName": "Leaf", "parentFolderId": "child"},
            ],
        )
        result = await client.post(f"/_mock/tenants/{tenant}/mail/content/import", json=batch)
        assert result.status_code == 200, result.text
        async with database.sessions() as session:
            assert (
                await session.get(MailboxFolder, (tenant, user, "grandchild"))
            ).path == "/Inbox/Child/Leaf"
        batch.update(
            expected_revision=2,
            folders=[{"id": "child", "displayName": "Renamed", "parentFolderId": "archive-local"}],
        )
        result = await client.post(f"/_mock/tenants/{tenant}/mail/content/import", json=batch)
        assert result.status_code == 200, result.text
        async with database.sessions() as session:
            assert (
                await session.get(MailboxFolder, (tenant, user, "child"))
            ).path == "/Archive/Renamed"
            assert (
                await session.get(MailboxFolder, (tenant, user, "grandchild"))
            ).path == "/Archive/Renamed/Leaf"

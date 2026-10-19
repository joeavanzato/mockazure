"""Graph mail actions persist local delivery through the actual API and worker."""

import base64
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from mockazure.jobs.dispatch import dispatch_job
from mockazure.jobs.repository import claim_job
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_mail_content import seed_mail
from tests.integration.test_mail_content_delta import collect


async def setup_mail(client, database, settings, tenant, app, user):
    await seed_mail(client, database, tenant, user)
    response = await client.post(
        f"/_mock/tenants/{tenant}/identity/users",
        json={
            "upn": "recipient@contoso.test",
            "display_name": "Recipient",
            "password": "local-recipient-password",
        },
    )
    assert response.status_code == 201, response.text
    recipient = UUID(response.json()["id"])
    provisioned = await client.put(
        f"/_mock/tenants/{tenant}/mail/mailboxes/{recipient}",
        json={"primary_smtp": "recipient@contoso.test", "alias": "recipient"},
    )
    assert provisioned.status_code == 200, provisioned.text
    write = await headers_for(database, settings, tenant, app, None, {"Mail.ReadWrite"})
    send = await headers_for(database, settings, tenant, app, None, {"Mail.Send"})
    return recipient, write, send


async def deliver(database, tenant):
    now = datetime.now(UTC)
    async with database.sessions.begin() as session:
        claim = await claim_job(
            session,
            owner=uuid4(),
            now=now,
            lease_seconds=30,
            tenant_id=tenant,
            kind="mail.delivery",
        )
    assert claim is not None
    await database.engine.dispose()
    assert (await dispatch_job(database, claim, now)).state == "succeeded"
    assert (await dispatch_job(database, claim, now)).state == "stale"


def message(subject="Local workflow"):
    return {
        "subject": subject,
        "body": {"contentType": "text", "content": "Local body"},
        "toRecipients": [{"emailAddress": {"address": "recipient@contoso.test"}}],
        "attachments": [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": "evidence.bin",
                "contentBytes": base64.b64encode(b"attachment\x00bytes").decode(),
            }
        ],
    }


@pytest.mark.parametrize("mime", [False, True])
async def test_sendmail_wire_and_local_delivery(database, settings, mime):
    async with graph_client(database, settings) as (client, tenant, app, user):
        recipient, read, send = await setup_mail(client, database, settings, tenant, app, user)
        root = f"/v1.0/users/{user}"
        if mime:
            from email.message import EmailMessage

            mail = EmailMessage()
            mail["Subject"] = "Local workflow"
            mail["To"] = "recipient@contoso.test"
            mail.set_content("Local body")
            mail.add_attachment(
                b"attachment\x00bytes",
                maintype="application",
                subtype="octet-stream",
                filename="evidence.bin",
            )
            accepted = await client.post(
                root + "/sendMail",
                headers=send | {"Content-Type": "text/plain"},
                content=base64.b64encode(mail.as_bytes()),
            )
        else:
            accepted = await client.post(
                root + "/sendMail", headers=send, json={"message": message()}
            )
        assert accepted.status_code == 202 and accepted.content == b"", accepted.text
        # Permissions to send do not imply mailbox read or write access.
        assert (await client.get(root + "/messages", headers=send)).status_code == 403
        await deliver(database, tenant)
        sent, _ = await collect(client, root + "/mailFolders/sentitems/messages", read)
        received, delta = await collect(
            client, f"/v1.0/users/{recipient}/mailFolders/inbox/messages/delta", read
        )
        assert len(sent) == len(received) == 1
        assert sent[0]["subject"] == received[0]["subject"] == "Local workflow"
        assert (
            not sent[0]["isDraft"]
            and sent[0]["internetMessageId"] == received[0]["internetMessageId"]
        )
        path = f"/v1.0/users/{recipient}/messages/{received[0]['id']}/attachments"
        attachments = (await client.get(path, headers=read)).json()["value"]
        raw = await client.get(path + "/" + attachments[0]["id"] + "/$value", headers=read)
        assert raw.content == b"attachment\x00bytes"
        assert (
            await client.post(root + "/sendMail", headers=read, json={"message": message()})
        ).status_code == 403
        assert (
            await client.post(
                root + "/sendMail",
                headers=send | {"Content-Type": "text/plain"},
                content=b"bad base64!",
            )
        ).status_code == 400
        assert (await collect(client, delta, read))[0] == []


async def test_draft_reply_forward(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        recipient, write, send = await setup_mail(client, database, settings, tenant, app, user)
        root = f"/v1.0/users/{user}"
        draft = await client.post(root + "/messages", headers=write, json=message())
        assert draft.status_code == 201, draft.text
        assert draft.json()["isDraft"]
        url = root + "/messages/" + draft.json()["id"]
        changed = await client.patch(url, headers=write, json={"subject": "Changed draft"})
        assert changed.status_code == 200 and changed.json()["subject"] == "Changed draft"
        assert (await client.post(url + "/send", headers=send)).status_code == 202
        await deliver(database, tenant)
        received, _ = await collect(client, f"/v1.0/users/{recipient}/messages", write)
        source = f"/v1.0/users/{recipient}/messages/{received[0]['id']}"
        for action in ["createReply", "createReplyAll", "createForward"]:
            body = {"comment": "Local response"}
            if action == "createForward":
                body["toRecipients"] = [{"emailAddress": {"address": "reader@contoso.test"}}]
            reply = await client.post(source + "/" + action, headers=write, json=body)
            assert reply.status_code == 201 and reply.json()["isDraft"], reply.text
            assert (
                reply.json()["toRecipients"][0]["emailAddress"]["address"] == "reader@contoso.test"
            )
            assert reply.json()["conversationId"] == received[0]["conversationId"]
            if action == "createForward":
                attachments = await client.get(
                    f"/v1.0/users/{recipient}/messages/{reply.json()['id']}/attachments",
                    headers=write,
                )
                assert len(attachments.json()["value"]) == 1
        invalid = await client.post(
            source + "/reply",
            headers=send,
            json={"comment": "one", "message": {"body": {"contentType": "text", "content": "two"}}},
        )
        assert invalid.status_code == 400
        accepted = await client.post(source + "/reply", headers=send, json={"comment": "Reply"})
        assert accepted.status_code == 202 and not accepted.content
        await deliver(database, tenant)
        assert (
            await client.patch(source, headers=write, json={"subject": "Not a draft"})
        ).status_code == 400


async def test_attachment_upload_contract(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, write, _ = await setup_mail(client, database, settings, tenant, app, user)
        root = f"/v1.0/users/{user}/messages"
        draft = await client.post(root, headers=write, json={"subject": "Upload target"})
        assert draft.status_code == 201, draft.text
        path = root + "/" + draft.json()["id"] + "/attachments"
        data = b"x" * (3 * 1024 * 1024 + 3)
        result = await client.post(
            path + "/createUploadSession",
            headers=write,
            json={
                "AttachmentItem": {"attachmentType": "file", "name": "large.bin", "size": len(data)}
            },
        )
        assert result.status_code == 201, result.text
        session = result.json()
        url = session["uploadUrl"]
        assert "graph.microsoft.com" not in url and session["expirationDateTime"]
        bad = await client.put(
            url,
            content=data[:100],
            headers={
                "Content-Range": f"bytes 10-109/{len(data)}",
                "Content-Type": "application/octet-stream",
            },
        )
        assert bad.status_code == 416, bad.text
        first = await client.put(
            url,
            content=data[: 1024 * 1024],
            headers={
                "Content-Range": f"bytes 0-1048575/{len(data)}",
                "Content-Type": "application/octet-stream",
            },
        )
        assert first.status_code == 200 and first.json()["nextExpectedRanges"] == ["1048576"]
        await database.engine.dispose()
        last = await client.put(
            url,
            content=data[1024 * 1024 :],
            headers={
                "Content-Range": f"bytes 1048576-{len(data) - 1}/{len(data)}",
                "Content-Type": "application/octet-stream",
            },
        )
        assert last.status_code == 201 and last.headers["Location"], last.text
        raw = await client.get(last.headers["Location"] + "/$value", headers=write)
        assert raw.content == data
        assert (
            await client.put(
                url,
                content=b"x",
                headers={
                    "Content-Range": f"bytes 0-0/{len(data)}",
                    "Content-Type": "application/octet-stream",
                },
            )
        ).status_code == 404
        again = await client.post(
            path + "/createUploadSession",
            headers=write,
            json={
                "AttachmentItem": {
                    "attachmentType": "file",
                    "name": "expired.bin",
                    "size": len(data),
                }
            },
        )
        client._transport.app.state.clock = lambda: datetime.now(UTC) + timedelta(days=2)
        assert (
            await client.put(
                again.json()["uploadUrl"],
                content=data,
                headers={
                    "Content-Range": f"bytes 0-{len(data) - 1}/{len(data)}",
                    "Content-Type": "application/octet-stream",
                },
            )
        ).status_code == 404


async def test_folder_scoped_draft_and_upload_location_schema(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, write, _ = await setup_mail(client, database, settings, tenant, app, user)
        root = f"/v1.0/users/{user}"
        draft = await client.post(
            root + "/mailFolders/archive/messages", headers=write, json={"subject": "Scoped draft"}
        )
        assert draft.status_code == 201, draft.text
        assert draft.json()["parentFolderId"] == "archive-local"
        path = root + "/messages/" + draft.json()["id"] + "/attachments"
        session = await client.post(
            path + "/createUploadSession",
            headers=write,
            json={
                "AttachmentItem": {
                    "attachmentType": "file",
                    "name": "large.bin",
                    "size": 3 * 1024 * 1024,
                }
            },
        )
        url = session.json()["uploadUrl"]
        wrong = await client.put(
            url,
            content=b"x",
            headers={"Content-Range": "bytes 0-0/3145728", "Content-Type": "text/plain"},
        )
        assert wrong.status_code == 400
        result = await client.put(
            url,
            content=b"x" * (3 * 1024 * 1024),
            headers={
                "Content-Range": "bytes 0-3145727/3145728",
                "Content-Type": "application/octet-stream",
            },
        )
        assert result.status_code == 201, result.text
        assert (
            "/api/v2.0/Users('" in result.headers["Location"]
            and "/Attachments('" in result.headers["Location"]
        )
        assert (
            await client.get(result.headers["Location"] + "/$value", headers=write)
        ).content == b"x" * (3 * 1024 * 1024)


async def test_sendmail_repeats_and_save_sent_false(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        recipient, read, send = await setup_mail(client, database, settings, tenant, app, user)
        for _ in range(2):
            result = await client.post(
                f"/v1.0/users/{user}/sendMail",
                headers=send,
                json={"message": message(), "saveToSentItems": False},
            )
            assert result.status_code == 202 and not result.content
            await deliver(database, tenant)
        received, _ = await collect(client, f"/v1.0/users/{recipient}/messages", read)
        assert (
            len(received) == 2
            and received[0]["internetMessageId"] != received[1]["internetMessageId"]
        )
        sent = await client.get(f"/v1.0/users/{user}/mailFolders/sentitems/messages", headers=read)
        assert sent.status_code == 404 or sent.json()["value"] == []


async def test_send_delivery_lease_expiry_rolls_back(database, settings):
    from unittest.mock import patch

    from mockazure.mail import send as module

    async with graph_client(database, settings) as (client, tenant, app, user):
        recipient, read, send = await setup_mail(client, database, settings, tenant, app, user)
        assert (
            await client.post(
                f"/v1.0/users/{user}/sendMail", headers=send, json={"message": message()}
            )
        ).status_code == 202
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            claim = await claim_job(
                session,
                owner=uuid4(),
                now=now,
                lease_seconds=30,
                tenant_id=tenant,
                kind="mail.delivery",
            )
        original = module.mail_fact
        expired = False

        async def expire(*args, **kwargs):
            nonlocal expired
            result = await original(*args, **kwargs)
            expired = True
            return result

        with (
            patch.object(module, "mail_fact", expire),
            patch.object(module, "monotonic", lambda: 100.0 if expired else 0.0),
        ):
            assert (await dispatch_job(database, claim, now)).state == "stale"
        assert (await collect(client, f"/v1.0/users/{recipient}/messages", read))[0] == []
        assert (await dispatch_job(database, claim, now)).state == "succeeded"
        assert len((await collect(client, f"/v1.0/users/{recipient}/messages", read))[0]) == 1


async def test_mail_import_retires_pending_send_and_upload_authority(database, settings):
    from mockazure.mail.messages import state_for
    from mockazure.mail.repository import resolve_mailbox

    async with graph_client(database, settings) as (client, tenant, app, user):
        recipient, read, send = await setup_mail(client, database, settings, tenant, app, user)
        root = f"/v1.0/users/{user}"
        draft = await client.post(
            root + "/messages", headers=read, json={"subject": "upload target"}
        )
        upload = await client.post(
            root + "/messages/" + draft.json()["id"] + "/attachments/createUploadSession",
            headers=read,
            json={
                "AttachmentItem": {"attachmentType": "file", "name": "large.bin", "size": 3145728}
            },
        )
        assert upload.status_code == 201, upload.text
        assert (
            await client.post(root + "/sendMail", headers=send, json={"message": message()})
        ).status_code == 202
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            claim = await claim_job(
                session,
                owner=uuid4(),
                now=now,
                lease_seconds=30,
                tenant_id=tenant,
                kind="mail.delivery",
            )
            box = await resolve_mailbox(session, tenant, str(user))
            revision = (await state_for(session, box)).revision
        imported = await client.post(
            f"/_mock/tenants/{tenant}/mail/content/import",
            json={
                "mailbox_user_id": str(user),
                "mode": "content-backed",
                "expected_revision": revision,
                "messages": [
                    {
                        "id": "fixture-added",
                        "folder_id": "inbox",
                        "payload": {"subject": "restored"},
                    }
                ],
            },
        )
        assert imported.status_code == 200, imported.text
        assert (await dispatch_job(database, claim, now)).state == "stale"
        assert (
            await client.put(
                upload.json()["uploadUrl"],
                content=b"x",
                headers={
                    "Content-Range": "bytes 0-0/3145728",
                    "Content-Type": "application/octet-stream",
                },
            )
        ).status_code == 404
        assert (await collect(client, f"/v1.0/users/{recipient}/messages", read))[0] == []

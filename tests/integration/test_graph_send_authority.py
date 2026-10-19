"""Mail workflow authority, cleanup and action-specific wire consequences."""

from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from uuid import uuid4

from sqlalchemy import func, select

from mockazure.jobs.dispatch import dispatch_job
from mockazure.jobs.repository import claim_job
from mockazure.mail.message_cleanup import cleanup_mail_content
from mockazure.mail.send_models import MailAttachmentUpload
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_exchange_mail import command
from tests.integration.test_exchange_recipients import setup as delegate_setup
from tests.integration.test_graph_send_workflows import deliver, message, setup_mail
from tests.integration.test_mail_content_delta import collect


async def test_send_delegation_revoked_between_acceptance_and_worker(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        exo, delegate = await delegate_setup(client, database, settings, tenant, app, owner)
        grant = {"Identity": str(owner), "Trustee": str(delegate), "AccessRights": ["SendAs"]}
        assert (
            await command(client, tenant, exo, "Add-RecipientPermission", grant)
        ).status_code == 200
        delegated = await headers_for(
            database, settings, tenant, app, delegate, {"Mail.Send.Shared"}, kind="delegated"
        )
        read = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
        body = {
            "message": {
                "subject": "Delegated",
                "from": {"emailAddress": {"address": "reader@contoso.test"}},
                "toRecipients": [{"emailAddress": {"address": "reader@contoso.test"}}],
            }
        }
        assert (
            await client.post("/v1.0/me/sendMail", headers=delegated, json=body)
        ).status_code == 202
        assert (
            await command(client, tenant, exo, "Remove-RecipientPermission", grant)
        ).status_code == 200
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
        result = await dispatch_job(database, claim, now)
        assert result.state == "failed" and result.error_code == "MailDeliveryAuthorizationRevoked"
        assert (await collect(client, f"/v1.0/users/{owner}/messages", read))[0] == []
        assert (
            await client.post("/v1.0/me/sendMail", headers=delegated, json=body)
        ).status_code == 403
        assert (
            await command(
                client,
                tenant,
                exo,
                "Set-Mailbox",
                {"Identity": str(owner), "GrantSendOnBehalfTo": [str(delegate)]},
            )
        ).status_code == 200
        body["message"]["sender"] = {"emailAddress": {"address": "delegate@contoso.test"}}
        assert (
            await client.post("/v1.0/me/sendMail", headers=delegated, json=body)
        ).status_code == 202
        await deliver(database, tenant)
        received = (await collect(client, f"/v1.0/users/{owner}/messages", read))[0]
        assert len(received) == 1
        assert received[0]["from"]["emailAddress"]["address"] == "reader@contoso.test"
        assert received[0]["sender"]["emailAddress"]["address"] == "delegate@contoso.test"


async def test_expired_uploads_participate_in_mail_cleanup(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, write, _ = await setup_mail(client, database, settings, tenant, app, user)
        root = f"/v1.0/users/{user}/messages"
        draft = await client.post(root, headers=write, json={"subject": "expire"})
        response = await client.post(
            root + "/" + draft.json()["id"] + "/attachments/createUploadSession",
            headers=write,
            json={
                "AttachmentItem": {"attachmentType": "file", "name": "expire.bin", "size": 3145728}
            },
        )
        assert response.status_code == 201
        async with database.sessions.begin() as session:
            await cleanup_mail_content(session, tenant, datetime.now(UTC) + timedelta(days=2), 100)
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(MailAttachmentUpload)
                    .where(MailAttachmentUpload.tenant_id == tenant)
                )
                == 0
            )


async def test_reply_mime_preserves_thread_references(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        recipient, write, send = await setup_mail(client, database, settings, tenant, app, user)
        assert (
            await client.post(
                f"/v1.0/users/{user}/sendMail", headers=send, json={"message": message()}
            )
        ).status_code == 202
        await deliver(database, tenant)
        received = (await collect(client, f"/v1.0/users/{recipient}/messages", write))[0][0]
        root = f"/v1.0/users/{recipient}/messages/{received['id']}"
        reply = await client.post(
            root + "/createReply", headers=write, json={"comment": "reply content"}
        )
        raw = await client.get(
            f"/v1.0/users/{recipient}/messages/{reply.json()['id']}/$value", headers=write
        )
        mime = BytesParser(policy=policy.default).parsebytes(raw.content)
        assert mime["In-Reply-To"] == received["internetMessageId"]
        assert received["internetMessageId"] in mime["References"]
        assert mime["Message-ID"] != received["internetMessageId"]


async def test_item_and_reference_attachment_creation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, write, _ = await setup_mail(client, database, settings, tenant, app, user)
        root = f"/v1.0/users/{user}/messages"
        draft = await client.post(root, headers=write, json={"subject": "Attachment types"})
        path = root + "/" + draft.json()["id"] + "/attachments"
        nested = {
            "@odata.type": "#microsoft.graph.message",
            "subject": "Nested evidence",
            "body": {"contentType": "text", "content": "nested body"},
        }
        item = await client.post(
            path,
            headers=write,
            json={
                "@odata.type": "#microsoft.graph.itemAttachment",
                "name": "evidence.eml",
                "item": nested,
            },
        )
        assert item.status_code == 201, item.text
        expanded = await client.get(
            path + "/" + item.json()["id"] + "?$expand=microsoft.graph.itemAttachment/item",
            headers=write,
        )
        assert expanded.status_code == 200, expanded.text
        assert expanded.json()["item"]["subject"] == "Nested evidence"
        raw = await client.get(path + "/" + item.json()["id"] + "/$value", headers=write)
        assert (
            BytesParser(policy=policy.default).parsebytes(raw.content)["Subject"]
            == "Nested evidence"
        )
        parent = await client.get(root + "/" + draft.json()["id"] + "/$value", headers=write)
        parent_mime = BytesParser(policy=policy.default).parsebytes(parent.content)
        enclosed = list(parent_mime.iter_attachments())[0].get_payload(0)
        assert enclosed["Subject"] == "Nested evidence"
        assert enclosed.get_content().strip() == "nested body"
        reference = await client.post(
            path,
            headers=write,
            json={
                "@odata.type": "#microsoft.graph.referenceAttachment",
                "name": "Imported reference metadata",
            },
        )
        assert reference.status_code == 201, reference.text
        assert (
            await client.get(path + "/" + reference.json()["id"] + "/$value", headers=write)
        ).status_code == 405
        assert (
            await client.delete(path + "/" + item.json()["id"], headers=write)
        ).status_code == 204


async def test_evidence_reset_invalidates_accepted_delivery(database, settings):
    from mockazure.evidence.repository import lock_state

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
            generation = (await lock_state(session, tenant)).generation
        reset = await client.post(
            f"/_mock/tenants/{tenant}/evidence/reset", json={"expected_generation": str(generation)}
        )
        assert reset.status_code == 200, reset.text
        result = await dispatch_job(database, claim, now)
        assert result.state in {"stale", "failed"}
        assert (await collect(client, f"/v1.0/users/{recipient}/messages", read))[0] == []


async def test_deleted_sender_terminates_delivery_without_escaping_worker(database, settings):
    from mockazure.identity.models import User

    async with graph_client(database, settings) as (client, tenant, app, user):
        recipient, read, send = await setup_mail(client, database, settings, tenant, app, user)
        response = await client.post(
            f"/v1.0/users/{user}/sendMail", headers=send, json={"message": message()}
        )
        assert response.status_code == 202
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
            sender = await session.get(User, user)
            sender.deleted_at = now
        result = await dispatch_job(database, claim, now)
        assert result.state == "failed" and result.error_code == "MailDeliveryInvalidated"
        assert (await collect(client, f"/v1.0/users/{recipient}/messages", read))[0] == []


async def test_mail_workflow_upgrade_preserves_content_and_guards_downgrade(database, settings):
    import pytest
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError

    from mockazure.db.migrations import configuration

    async with graph_client(database, settings) as (client, tenant, app, user):
        _, read, send = await setup_mail(client, database, settings, tenant, app, user)
        root = f"/v1.0/users/{user}/messages"
        draft = await client.post(root, headers=read, json={"subject": "Upgrade retained draft"})
        assert draft.status_code == 201
        engine = create_engine(settings.database_url.get_secret_value())
        try:
            config = configuration()
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "067")
                command.upgrade(config, "head")
            await database.engine.dispose()
            loaded = await client.get(root + "/" + draft.json()["id"], headers=read)
            assert loaded.status_code == 200
            assert loaded.json()["subject"] == "Upgrade retained draft"
            accepted = await client.post(
                f"/v1.0/users/{user}/sendMail", headers=send, json={"message": message()}
            )
            assert accepted.status_code == 202
            with pytest.raises(DBAPIError, match="Reset mail workflow state before downgrade"):
                with engine.begin() as connection:
                    config.attributes["connection"] = connection
                    command.downgrade(config, "067")
            await deliver(database, tenant)
        finally:
            engine.dispose()

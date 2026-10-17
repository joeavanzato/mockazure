from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from mockazure.evidence.models import EvidenceObservation, EvidenceSource
from mockazure.providers.graph.schema_reference import validate_payload
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_evidence_directory import drain
from tests.integration.test_exchange_mail import command, exchange_headers
from tests.integration.test_graph_mail_rules import body
from tests.integration.test_mailbox_state import actor, provision


async def setup(client, tenant, user, app):
    await provision(client, tenant, user)
    assert (
        await client.put(
            f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/Exchange Administrator"
        )
    ).status_code == 200
    assert (
        await client.patch(f"/_mock/tenants/{tenant}/evidence/settings", json={"delay_seconds": 0})
    ).status_code == 200


async def mail_sources(database, tenant):
    async with database.sessions() as session:
        return list(
            await session.scalars(
                select(EvidenceSource)
                .where(EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "mail")
                .order_by(EvidenceSource.source_revision)
            )
        )


async def observations(database, tenant):
    async with database.sessions() as session:
        return list(
            await session.scalars(
                select(EvidenceObservation).where(
                    EvidenceObservation.tenant_id == tenant,
                    EvidenceObservation.family == "auditLogRecord",
                )
            )
        )


async def test_native_mail_audit_atomicity(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await setup(client, tenant, user, app)
        headers = await exchange_headers(database, settings, tenant, app)
        params = {"Identity": str(user), "ForwardingSmtpAddress": "smtp:outside@example.test"}
        assert (await command(client, tenant, headers, "Set-Mailbox", params)).status_code == 200
        assert (await command(client, tenant, headers, "Set-Mailbox", params)).status_code == 200
        assert len(await mail_sources(database, tenant)) == 1
        from mockazure.mail.mailboxes import mutate_mailbox
        from mockazure.mail.repository import resolve_mailbox

        with pytest.raises(RuntimeError, match="rollback"):
            async with database.sessions.begin() as session:
                box = await resolve_mailbox(session, tenant, str(user))
                await mutate_mailbox(
                    session,
                    actor(tenant, app),
                    box,
                    {"ForwardingSmtpAddress": "smtp:rollback@example.test"},
                    datetime.now(UTC),
                )
                raise RuntimeError("rollback")
        assert len(await mail_sources(database, tenant)) == 1
        graph = await headers_for(
            database, settings, tenant, app, None, {"MailboxSettings.ReadWrite"}
        )
        path = f"/v1.0/users/{user}/mailFolders/inbox/messageRules"
        rule = await client.post(path, headers=graph, json=body("Audit rule"))
        assert rule.status_code == 201, rule.text
        assert (
            await client.patch(
                path + "/" + rule.json()["id"], headers=graph, json={"displayName": "Audit rule"}
            )
        ).status_code == 200
        assert len(await mail_sources(database, tenant)) == 2
        await drain(database)
        rows = await observations(database, tenant)
        assert len(rows) == 2
        assert {row.payload["operation"] for row in rows} == {"Set-Mailbox", "New-InboxRule"}
        for row in rows:
            payload = row.payload
            validate_payload("v1.0", "security.auditLogRecord", payload)
            assert payload["service"] == "Exchange"
            assert payload["organizationId"] == str(tenant)
            assert payload["userType"] == "ServicePrincipal"
            data = payload["auditData"]["dynamicProperties"]
            assert data["Operation"] == payload["operation"]
            assert data["MailboxOwnerUPN"] == "reader@contoso.test"
            assert data["ResultStatus"] == "True"
            assert "Authorization" not in data and "client_secret" not in data
        mailbox = next(r.payload for r in rows if r.payload["operation"] == "Set-Mailbox")
        assert mailbox["auditData"]["dynamicProperties"]["ModifiedProperties"] == [
            {
                "Name": "ForwardingSmtpAddress",
                "OldValue": "",
                "NewValue": "smtp:outside@example.test",
            }
        ]


async def test_mail_audit_replay_and_rename(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await setup(client, tenant, user, app)
        headers = await exchange_headers(database, settings, tenant, app)
        assert (
            await command(
                client,
                tenant,
                headers,
                "Set-Mailbox",
                {"Identity": str(user), "DeliverToMailboxAndForward": True},
            )
        ).status_code == 200
        sources = await mail_sources(database, tenant)
        old_facts = sources[0].facts
        graph = await headers_for(database, settings, tenant, app, None, {"User.ReadWrite.All"})
        renamed = await client.patch(
            f"/v1.0/users/{user}", headers=graph, json={"userPrincipalName": "renamed@contoso.test"}
        )
        assert renamed.status_code == 204, renamed.text
        # Fresh worker invocations recover persisted intent; no request-local queue.
        await drain(database)
        first = (await observations(database, tenant))[0].payload
        assert first["objectId"] == "reader@contoso.test"
        from mockazure.evidence.repository import load_source, publish_batch
        from mockazure.evidence.schemas import SourceRef
        from mockazure.mail.audit import project_mail

        async with database.sessions.begin() as session:
            source = await load_source(
                session, SourceRef(tenant_id=tenant, kind="mail", id=sources[0].id)
            )
            assert source.facts == old_facts
            replay = await publish_batch(session, project_mail(source))
            assert replay.already_applied is True
        assert len(await observations(database, tenant)) == 1


async def test_historical_ual_import_export(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await setup(client, tenant, user, app)
        when = datetime.now(UTC) - timedelta(hours=1)
        identifier = str(uuid4())
        payload = {
            "id": identifier,
            "createdDateTime": when.isoformat(),
            "organizationId": str(tenant),
            "operation": "FileAccessed",
            "service": "OneDrive",
            "objectId": "/documents/report",
            "userId": "external@example.test",
            "userPrincipalName": "external@example.test",
            "userType": "Regular",
            "auditLogRecordType": "OneDrive",
            "auditData": {
                "dynamicProperties": {"Operation": "FileAccessed", "ObjectId": "/documents/report"}
            },
        }
        dataset = {
            "schema_version": 1,
            "provenance": {"source": "historic-qualified"},
            "records": [
                {
                    "family": "auditLogRecord",
                    "payload": payload,
                    "occurred_at": when.isoformat(),
                    "available_at": when.isoformat(),
                }
            ],
        }
        base = f"/_mock/tenants/{tenant}/evidence"
        result = await client.post(base + "/import", json=dataset)
        assert result.status_code == 200, result.text
        await drain(database)
        exported = await client.get(base + "/export?family=auditLogRecord")
        assert exported.status_code == 200, exported.text
        record = exported.json()["records"][0]
        assert record["payload"] == payload
        assert record["provenance"]["source"] == "historic-qualified"
        assert record["api_version"] == "v1.0"
        replay = await client.post(base + "/import", json=exported.json())
        assert replay.status_code == 200 and replay.json()["unchanged"] == 1
        payload["id"] = str(uuid4())
        payload["organizationId"] = str(uuid4())
        assert (await client.post(base + "/import", json=dataset)).status_code == 422
        payload["organizationId"] = str(tenant)
        payload["auditData"]["dynamicProperties"]["Authorization"] = "Bearer forbidden"
        assert (await client.post(base + "/import", json=dataset)).status_code == 422


async def test_mail_audit_survives_worker_process_and_api_recreation(database, settings):
    import json
    import subprocess
    import sys

    import httpx

    from mockazure.admin.app import create_app
    from mockazure.db.engine import close_database, create_database

    async with graph_client(database, settings) as (client, tenant, app, user):
        await setup(client, tenant, user, app)
        headers = await exchange_headers(database, settings, tenant, app)
        assert (
            await command(
                client,
                tenant,
                headers,
                "Set-Mailbox",
                {"Identity": str(user), "ForwardingSmtpAddress": "smtp:durable@example.test"},
            )
        ).status_code == 200
        config = settings.model_dump(mode="json")
        config["database_url"] = settings.database_url.get_secret_value()
        program = """
import asyncio,sys
from datetime import UTC,datetime,timedelta
from uuid import uuid4
from mockazure.config import Settings
from mockazure.db.engine import create_database,close_database
from mockazure.jobs.worker import run_one
async def run():
    db=create_database(Settings.model_validate_json(sys.stdin.read()))
    try:
        for _ in range(100):
            if not await run_one(db,owner=uuid4(),now=datetime.now(UTC)+timedelta(seconds=1)):
                return
        raise RuntimeError("undrained")
    finally:
        await close_database(db)
try:
    from mockazure.asyncio_support import run as run_async
    run_async(run())
except Exception as error:
    print(type(error).__name__)
    sys.exit(1)
"""
        process = subprocess.run(
            [sys.executable, "-c", program],
            input=json.dumps(config),
            text=True,
            capture_output=True,
            timeout=40,
        )
        assert process.returncode == 0, process.stdout
        fresh = create_database(settings)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(settings, fresh)),
                base_url="https://localhost:8443",
                headers={"Authorization": "Bearer " + "A" * 43},
            ) as restarted:
                result = await restarted.get(
                    f"/_mock/tenants/{tenant}/evidence/export?family=auditLogRecord"
                )
                assert result.status_code == 200, result.text
                rows = result.json()["records"]
                assert len(rows) == 1
                assert rows[0]["payload"]["operation"] == "Set-Mailbox"
        finally:
            await close_database(fresh)

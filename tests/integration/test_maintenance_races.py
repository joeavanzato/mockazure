import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from mockazure.auditlog.models import AuditQuery
from mockazure.db.job_model import Job
from mockazure.evidence.models import EvidenceCursor
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_audit_queries import PATH, claim, entitlement, query_body, seed
from tests.integration.test_graph_mail_rules import body
from tests.integration.test_mailbox_state import provision
from tests.integration.test_maintenance import cleanup_path, count


async def test_cleanup_during_page_capture_and_rule_mutation(database, settings, monkeypatch):
    from mockazure.providers.graph import mail_routes

    captured, release = asyncio.Event(), asyncio.Event()
    original = mail_routes.issue_cursor

    async def paused(session, state, **kwargs):
        captured.set()
        await release.wait()
        return await original(session, state, **kwargs)

    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(client, tenant, user)
        headers = await headers_for(
            database, settings, tenant, app, None, {"MailboxSettings.ReadWrite"}
        )
        path = f"/v1.0/users/{user}/mailFolders/inbox/messageRules"
        values = []
        for i in range(3):
            values.append(
                (await client.post(path, headers=headers, json=body(str(i), i + 1))).json()
            )
        monkeypatch.setattr(mail_routes, "issue_cursor", paused)
        page = asyncio.create_task(client.get(path + "?$top=1", headers=headers))
        mutation = None
        try:
            await asyncio.wait_for(captured.wait(), 3)
            mutation = asyncio.create_task(
                client.patch(
                    path + "/" + values[1]["id"], headers=headers, json={"displayName": "After"}
                )
            )
            cleaned = await asyncio.wait_for(
                client.post(cleanup_path(tenant), json={"max_rows": 100}), 3
            )
            assert cleaned.status_code == 200
            await asyncio.sleep(0.1)
            assert not mutation.done()
        finally:
            release.set()
        first = await page
        assert (await mutation).status_code == 200
        await client.post(cleanup_path(tenant), json={"max_rows": 100})
        second = await client.get(first.json()["@odata.nextLink"], headers=headers)
        assert second.json()["value"][0] == values[1]


async def test_expired_job_fence_and_pin_release_rollback_together(database, settings):
    from mockazure.auditlog.cleanup import cleanup_audit

    async with graph_client(database, settings) as (client, tenant, app, user):
        await entitlement(client, tenant)
        await seed(database, tenant)
        headers = await headers_for(
            database, settings, tenant, app, None, {"AuditLogsQuery.Read.All"}
        )
        query = await client.post(PATH, headers=headers, json=query_body())
        identifier = UUID(query.json()["id"])
        leased = await claim(database, tenant)
        async with database.sessions.begin() as session:
            row = await session.get(AuditQuery, (tenant, identifier))
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with pytest.raises(RuntimeError, match="simulate failure"):
            async with database.sessions.begin() as session:
                result = await cleanup_audit(session, tenant, datetime.now(UTC), 1)
                assert result["fenced_jobs"] == result["pins"] == 1
                raise RuntimeError("simulate failure")
        async with database.sessions() as session:
            job = await session.scalar(select(Job).where(Job.id == leased.id))
            assert job.state == "running" and job.claim_epoch == leased.epoch
            assert job.lease_owner == leased.owner
        assert await count(database, EvidenceCursor) == 1


def test_upgrade_034_preserves_maintenance_indexes(settings):
    import psycopg
    from alembic import command
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    from mockazure.db.migrations import configuration, migrate

    url = settings.database_url.get_secret_value()
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "034")
    finally:
        engine.dispose()
    migrate(url)
    plain = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(plain) as connection:
        expected_head = ScriptDirectory.from_config(configuration()).get_current_head()
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            expected_head,
        )
        names = {
            r[0]
            for r in connection.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname='public'"
            )
        }
        assert {
            "audit_query_expiry_idx",
            "mail_rule_closed_idx",
            "directory_cursor_resource_idx",
        } <= names

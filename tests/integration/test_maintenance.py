import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from mockazure.auditlog.models import AuditQuery, AuditQueryItem
from mockazure.db.job_model import Job
from mockazure.directory.models import Continuation
from mockazure.evidence.models import EvidenceCursor
from mockazure.jobs.dispatch import dispatch_job
from mockazure.mail.models import Mailbox, MailRuleVersion
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_audit_queries import PATH, claim, entitlement, finish, query_body, seed
from tests.integration.test_graph_mail_rules import body
from tests.integration.test_mailbox_state import provision


def cleanup_path(tenant):
    return f"/_mock/tenants/{tenant}/maintenance/cleanup"


async def count(database, model, *conditions):
    async with database.sessions() as session:
        return await session.scalar(select(func.count()).select_from(model).where(*conditions))


async def test_expired_query_cleanup_budget_live_pin_and_tombstone(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await entitlement(client, tenant)
        await seed(database, tenant, count=5)
        headers = await headers_for(
            database, settings, tenant, app, None, {"AuditLogsQuery.Read.All"}
        )
        response = await client.post(PATH, headers=headers, json=query_body())
        expired_id = UUID(response.json()["id"])
        await finish(database, tenant)
        live = await client.post(PATH, headers=headers, json=query_body())
        live_id = UUID(live.json()["id"])
        async with database.sessions.begin() as session:
            row = await session.get(AuditQuery, (tenant, expired_id))
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        for expected in (3, 1, 0):
            response = await client.post(cleanup_path(tenant), json={"max_rows": 2})
            assert response.status_code == 200, response.text
            assert 0 <= response.json()["deleted"] <= 2
            assert await count(database, AuditQueryItem) == expected
            expired = await client.get(PATH + "/" + str(expired_id), headers=headers)
            assert expired.status_code == 410
        assert await count(database, AuditQuery) == 2
        assert await count(database, EvidenceCursor) == 1
        await finish(database, tenant)
        assert (await client.get(PATH + "/" + str(live_id) + "/records", headers=headers)).json()[
            "value"
        ]
        for bad in (0, 1001, True, "2"):
            assert (
                await client.post(cleanup_path(tenant), json={"max_rows": bad})
            ).status_code == 422
        assert (await client.post(cleanup_path(uuid4()), json={"max_rows": 2})).status_code == 404
        assert (
            await client.post(
                cleanup_path(tenant),
                json={"max_rows": 2},
                headers={"Authorization": "Bearer invalid"},
            )
        ).status_code == 401


@pytest.mark.parametrize("running", [False, True])
async def test_expired_query_fences_pending_and_running_jobs(database, settings, running):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await entitlement(client, tenant)
        await seed(database, tenant)
        headers = await headers_for(
            database, settings, tenant, app, None, {"AuditLogsQuery.Read.All"}
        )
        response = await client.post(PATH, headers=headers, json=query_body())
        identifier = UUID(response.json()["id"])
        stale_claim = await claim(database, tenant) if running else None
        async with database.sessions.begin() as session:
            query = await session.get(AuditQuery, (tenant, identifier))
            query.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            job_id = query.job_id
            job = await session.get(Job, job_id)
            epoch = job.claim_epoch
        response = await client.post(cleanup_path(tenant), json={"max_rows": 10})
        assert response.status_code == 200, response.text
        assert response.json()["audit"]["fenced_jobs"] == 1
        async with database.sessions() as session:
            job = await session.get(Job, job_id)
            assert job.state == "failed" and job.error_code == "AuditQueryExpired"
            assert job.claim_epoch == epoch + 1
            assert job.lease_owner is None and job.lease_until is None
        assert await count(database, EvidenceCursor) == 0
        if stale_claim:
            result = await dispatch_job(database, stale_claim, datetime.now(UTC))
            assert result.state == "stale"
        assert await count(database, AuditQueryItem) == 0


async def test_cleanup_rollback_preserves_query_and_tenant_boundaries(database, settings):
    from mockazure.auditlog.cleanup import cleanup_audit

    async with graph_client(database, settings) as (client, tenant, app, user):
        await entitlement(client, tenant)
        await seed(database, tenant)
        headers = await headers_for(
            database, settings, tenant, app, None, {"AuditLogsQuery.Read.All"}
        )
        response = await client.post(PATH, headers=headers, json=query_body())
        identifier = UUID(response.json()["id"])
        await finish(database, tenant)
        async with database.sessions.begin() as session:
            row = await session.get(AuditQuery, (tenant, identifier))
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        other = (
            await client.post(
                "/_mock/tenants",
                json={"slug": "other-cleanup", "display_name": "Other", "domain": "other.test"},
            )
        ).json()["id"]
        response = await client.post(cleanup_path(other), json={"max_rows": 100})
        assert response.status_code == 200
        assert response.json()["deleted"] == 0
        with pytest.raises(RuntimeError, match="rollback"):
            async with database.sessions.begin() as session:
                result = await cleanup_audit(session, tenant, datetime.now(UTC), 10)
                assert result["deleted"] == 3
                raise RuntimeError("rollback")
        assert await count(database, AuditQueryItem) == 3


async def test_rule_cleanup_pins_snapshot_and_preserves_current_rules(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(client, tenant, user)
        headers = await headers_for(
            database, settings, tenant, app, None, {"MailboxSettings.ReadWrite"}
        )
        path = f"/v1.0/users/{user}/mailFolders/inbox/messageRules"
        for i in range(3):
            assert (
                await client.post(path, headers=headers, json=body(str(i), i + 1))
            ).status_code == 201
        first = (await client.get(path + "?$top=1", headers=headers)).json()
        baseline = (await client.get(path, headers=headers)).json()["value"]
        target = baseline[1]["id"]
        assert (
            await client.patch(
                path + "/" + target, headers=headers, json={"displayName": "Changed"}
            )
        ).status_code == 200
        response = await client.post(cleanup_path(tenant), json={"max_rows": 100})
        assert response.status_code == 200, response.text
        old = await client.get(first["@odata.nextLink"], headers=headers)
        assert old.json()["value"][0] == baseline[1]
        async with database.sessions.begin() as session:
            for cursor in await session.scalars(select(Continuation)):
                cursor.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        before = await count(database, MailRuleVersion)
        deleted = 0
        for _ in range(10):
            result = (await client.post(cleanup_path(tenant), json={"max_rows": 1})).json()
            assert result["deleted"] <= 1
            deleted += result["mail"]["versions"]
        assert deleted > 0
        assert await count(database, MailRuleVersion) < before
        latest = (await client.get(path, headers=headers)).json()["value"]
        assert len(latest) == 3 and latest[1]["displayName"] == "Changed"
        assert (await client.get(first["@odata.nextLink"], headers=headers)).status_code == 400


async def test_first_rule_page_waits_for_mailbox_lock(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(client, tenant, user)
        headers = await headers_for(
            database, settings, tenant, app, None, {"MailboxSettings.ReadWrite"}
        )
        path = f"/v1.0/users/{user}/mailFolders/inbox/messageRules"
        for i in range(2):
            await client.post(path, headers=headers, json=body(str(i), i + 1))
        async with database.sessions.begin() as session:
            await session.get(Mailbox, (tenant, user), with_for_update=True)
            pending = asyncio.create_task(client.get(path + "?$top=1", headers=headers))
            await asyncio.sleep(0.1)
            assert not pending.done(), "Page capture bypassed the cleanup/mutation lock"
        result = await pending
        assert result.status_code == 200 and "@odata.nextLink" in result.json()

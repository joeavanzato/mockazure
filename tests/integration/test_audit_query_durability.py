from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy import func, select

from mockazure.admin.app import create_app
from mockazure.auditlog.models import AuditQuery, AuditQueryItem
from mockazure.db.engine import close_database, create_database
from mockazure.evidence.cleanup import cleanup_evidence, reset_evidence
from mockazure.evidence.models import EvidenceCursor, EvidenceVersion
from mockazure.evidence.repository import lock_state
from mockazure.jobs.dispatch import dispatch_job
from tests.integration.graph_support import graph_client, headers_for, registered_app
from tests.integration.test_audit_queries import PATH, claim, entitlement, finish, query_body, seed


async def test_query_snapshot_retention_and_restart(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await entitlement(client, tenant)
        when = datetime.now(UTC) - timedelta(days=20)
        expected = await seed(database, tenant, when=when)
        headers = await headers_for(
            database, settings, tenant, app, None, {"AuditLogsQuery.Read.All"}
        )
        response = await client.post(
            PATH,
            headers=headers,
            json=query_body(filterStartDateTime=(when - timedelta(hours=1)).isoformat()),
        )
        assert response.status_code == 201
        identifier = UUID(response.json()["id"])
        item = PATH + "/" + str(identifier)
        # Publication after POST must never leak into the captured query, even with old timestamps.
        await seed(database, tenant, count=1, when=when)
        async with database.sessions.begin() as session:
            state = await lock_state(session, tenant)
            state.settings = {"retention_days": 1}
        async with database.sessions.begin() as session:
            await cleanup_evidence(session, tenant, datetime.now(UTC), limit=1000)
        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceVersion)
                    .where(
                        EvidenceVersion.tenant_id == tenant,
                        EvidenceVersion.family == "auditLogRecord",
                    )
                )
                == 3
            )
            assert await session.scalar(select(func.count()).select_from(EvidenceCursor)) == 1
        # An explicit generation reset must not destroy a pinned search.
        async with database.sessions.begin() as session:
            state = await lock_state(session, tenant)
            generation = state.generation
        async with database.sessions.begin() as session:
            await reset_evidence(session, tenant, generation)
            await cleanup_evidence(session, tenant, datetime.now(UTC), limit=1000)
        # A new DB engine and API instance use only persisted query/job state.
        fresh = create_database(settings)
        try:
            await finish(fresh, tenant)
            async with database.sessions.begin() as session:
                await cleanup_evidence(session, tenant, datetime.now(UTC), limit=1000)
            transport = httpx.ASGITransport(app=create_app(settings, fresh))
            async with httpx.AsyncClient(
                transport=transport, base_url="https://localhost:8443"
            ) as restarted:
                records = await restarted.get(item + "/records", headers=headers)
                assert records.status_code == 200, records.text
                assert {r["id"] for r in records.json()["value"]} == {r["id"] for r in expected}
        finally:
            await close_database(fresh)
        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceVersion)
                    .where(
                        EvidenceVersion.tenant_id == tenant,
                        EvidenceVersion.family == "auditLogRecord",
                    )
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AuditQueryItem)
                    .where(AuditQueryItem.query_id == identifier)
                )
                == 3
            )


async def test_query_large_pages_replay_binding_and_expiry(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await entitlement(client, tenant)
        expected = await seed(database, tenant, count=1003)
        headers = await headers_for(
            database, settings, tenant, app, None, {"AuditLogsQuery.Read.All"}
        )
        response = await client.post(PATH, headers=headers, json=query_body())
        assert response.status_code == 201
        item = PATH + "/" + response.json()["id"]
        await finish(database, tenant)
        first = await client.get(item + "/records?$top=1000&$select=id,operation", headers=headers)
        assert first.status_code == 200, first.text
        assert len(first.json()["value"]) == 100
        link = first.json()["@odata.nextLink"]
        other_app = await registered_app(client, tenant)
        other = await headers_for(
            database, settings, tenant, other_app, None, {"AuditLogsQuery.Read.All"}
        )
        assert (await client.get(link, headers=other)).status_code == 400
        changed = await headers_for(
            database, settings, tenant, app, None, {"AuditLogsQuery.Read.All", "User.Read.All"}
        )
        assert (await client.get(link, headers=changed)).status_code == 400
        assert (await client.get(link + "&$top=1", headers=headers)).status_code == 400
        page = await client.get(link, headers=headers)
        assert (await client.get(link, headers=headers)).json() == page.json()
        seen = [r["id"] for r in first.json()["value"]]
        while True:
            assert page.status_code == 200, page.text
            value = page.json()
            assert all(set(r) == {"id", "operation"} for r in value["value"])
            seen.extend(r["id"] for r in value["value"])
            if "@odata.nextLink" not in value:
                break
            page = await client.get(value["@odata.nextLink"], headers=headers)
        assert len(seen) == len(set(seen)) == 1003
        assert set(seen) == {r["id"] for r in expected}
        client._transport.app.state.clock = lambda: datetime.now(UTC) + timedelta(hours=2)
        assert (await client.get(link, headers=headers)).status_code == 410
        client._transport.app.state.clock = lambda: datetime.now(UTC) + timedelta(days=8)
        assert (await client.get(item, headers=headers)).status_code == 410
        assert (await client.get(item + "/records", headers=headers)).status_code == 410


async def test_query_filters_keyword_and_unknown_options(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await entitlement(client, tenant)
        await seed(database, tenant)
        headers = await headers_for(
            database, settings, tenant, app, None, {"AuditLogsQuery.Read.All"}
        )
        for keyword in ["common-only", "Set-Mailbox", "needle-%", "needle-_"]:
            response = await client.post(
                PATH, headers=headers, json=query_body(keywordFilter=keyword)
            )
            assert response.status_code == 201
            await finish(database, tenant)
            records = await client.get(
                PATH + "/" + response.json()["id"] + "/records", headers=headers
            )
            assert records.json()["value"] == []
        response = await client.post(
            PATH, headers=headers, json=query_body(keywordFilter="NEEDLE-")
        )
        assert response.status_code == 201
        await finish(database, tenant)
        item = PATH + "/" + response.json()["id"]
        assert len((await client.get(item + "/records", headers=headers)).json()["value"]) == 3
        for extra in [
            {"unknown": True},
            {"recordCountLimit": 1},
            {"recordTypeFilters": ["invented"]},
            {"serviceFilters": [None]},
            {"keywordFilter": 4},
            {"displayName": "x" * 1025},
        ]:
            assert (
                await client.post(PATH, headers=headers, json=query_body(**extra))
            ).status_code == 400
        for query in [
            "?$expand=records",
            "?$top=0",
            "?$top=1001",
            "?$top=1&$top=2",
            "?$select=secret",
        ]:
            assert (await client.get(item + "/records" + query, headers=headers)).status_code == 400


async def test_query_lease_loss_rolls_back_materialization(database, settings, monkeypatch):
    from mockazure.auditlog import worker

    async with graph_client(database, settings) as (client, tenant, app, user):
        await entitlement(client, tenant)
        await seed(database, tenant)
        headers = await headers_for(
            database, settings, tenant, app, None, {"AuditLogsQuery.Read.All"}
        )
        response = await client.post(PATH, headers=headers, json=query_body())
        assert response.status_code == 201
        now = datetime.now(UTC)
        leased = await claim(database, tenant, now, lease=1)
        # Expire only at the post-materialization fence, after the SQL insert executes.
        ticks = iter([0, 0, 0, 0, 2])
        monkeypatch.setattr(worker, "monotonic", lambda: next(ticks, 2))
        result = await dispatch_job(database, leased, now)
        assert result.state == "stale"
        async with database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(AuditQueryItem)) == 0
            row = await session.get(AuditQuery, (tenant, UUID(response.json()["id"])))
            assert row.record_count is None
        monkeypatch.undo()
        winner = await claim(database, tenant, now + timedelta(seconds=2))
        assert (
            await dispatch_job(database, winner, now + timedelta(seconds=2))
        ).state == "succeeded"

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select

from mockazure.activity.models import Content
from mockazure.activity.publication import publish_activity
from mockazure.jobs.dispatch import dispatch_job
from mockazure.jobs.repository import claim_job
from tests.integration.graph_support import graph_client, headers_for, registered_app
from tests.integration.test_audit_queries import seed


async def setup(client, tenant):
    now = datetime.now(UTC)
    result = await client.patch(
        f"/_mock/tenants/{tenant}/evidence/settings",
        json={"clock_mode": "fixed", "fixed_time": now.isoformat()},
    )
    assert result.status_code == 200, result.text
    result = await client.patch(f"/_mock/tenants/{tenant}/activity/settings", json={"page_size": 2})
    assert result.status_code == 200, result.text
    return now


async def test_publication_snapshot_content_expiry_and_restore(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        now = await setup(client, tenant)
        path = f"/api/v1.0/{tenant}/activity/feed"
        admin = f"/_mock/tenants/{tenant}/activity"
        auth = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {"ActivityFeed.Read"},
            audience="https://manage.office.com",
        )
        assert (
            await client.post(
                path + "/subscriptions/start?contentType=Audit.Exchange", headers=auth
            )
        ).status_code == 200
        records = await seed(database, tenant, 5, when=now - timedelta(days=20))
        async with database.sessions.begin() as session:
            ids = await publish_activity(session, tenant, [UUID(r["id"]) for r in records], now)
            assert (
                await publish_activity(
                    session, tenant, [UUID(r["id"]) for r in records], now + timedelta(seconds=1)
                )
                == ids
            )
        await client.post(f"/_mock/tenants/{tenant}/evidence/clock/advance", json={"seconds": 1})
        first = await client.get(
            path + "/subscriptions/content?contentType=Audit.Exchange", headers=auth
        )
        assert first.status_code == 200, first.text
        assert len(first.json()) == 2 and "NextPageUri" in first.headers
        link = first.headers["NextPageUri"]
        other = await registered_app(client, tenant)
        other_auth = await headers_for(
            database,
            settings,
            tenant,
            other,
            None,
            {"ActivityFeed.Read"},
            audience="https://manage.office.com",
        )
        await client.post(
            path + "/subscriptions/start?contentType=Audit.Exchange", headers=other_auth
        )
        assert (await client.get(link, headers=other_auth)).status_code == 400
        later = await seed(database, tenant, 1, when=now - timedelta(hours=1))
        async with database.sessions.begin() as session:
            await publish_activity(session, tenant, [UUID(later[0]["id"])], now)
        await database.engine.dispose()
        descriptors = first.json()
        while link:
            response = await client.get(link, headers=auth)
            assert response.status_code == 200, response.text
            descriptors += response.json()
            link = response.headers.get("NextPageUri")
        assert len(descriptors) == 5
        events = []
        for item in descriptors:
            response = await client.get(item["contentUri"], headers=auth)
            assert response.status_code == 200, response.text
            events += response.json()
        assert {v["Id"] for v in events} == {v["id"] for v in records}
        assert all("auditData" not in v and v["Operation"] == "Set-Mailbox" for v in events)
        exported = await client.get(admin + "/export")
        assert exported.status_code == 200, exported.text
        manifest = exported.json()
        assert len(manifest["contents"]) == 6
        generation = (await client.get(admin + "/state")).json()["generation"]
        assert (
            await client.post(admin + "/reset", json={"expected_generation": generation})
        ).status_code == 200
        assert (await client.get(first.headers["NextPageUri"], headers=auth)).status_code == 400
        # Restore does not restore any authority; subscribe before restoring records.
        await client.post(path + "/subscriptions/start?contentType=Audit.Exchange", headers=auth)
        restored = await client.post(admin + "/restore", json=manifest)
        assert restored.status_code == 200, restored.text
        assert (
            len(
                (
                    await client.get(
                        path + "/subscriptions/content?contentType=Audit.Exchange", headers=auth
                    )
                ).json()
            )
            == 2
        )
        await client.post(
            f"/_mock/tenants/{tenant}/evidence/clock/advance", json={"seconds": 7 * 86400}
        )
        current = (await client.get(admin + "/export")).json()["contents"][0]
        expired = await client.get(path + "/audit/" + current["content_id"], headers=auth)
        assert expired.status_code == 400 and expired.json()["error"]["code"] == "AF20051"
        cleanup = await client.post(admin + "/cleanup", json={"max_rows": 100})
        assert cleanup.status_code == 200 and cleanup.json()["pruned_contents"] == 6


async def test_leased_publication_delay_retry_and_stop_restart(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        now = await setup(client, tenant)
        admin = f"/_mock/tenants/{tenant}/activity"
        path = f"/api/v1.0/{tenant}/activity/feed"
        auth = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {"ActivityFeed.Read"},
            audience="https://manage.office.com",
        )
        await client.post(path + "/subscriptions/start?contentType=Audit.Exchange", headers=auth)
        await client.patch(admin + "/settings", json={"delay_seconds": 10, "fail_attempts": 1})
        records = await seed(database, tenant, 1, when=now - timedelta(hours=1))

        async def claim(when):
            async with database.sessions.begin() as session:
                return await claim_job(
                    session,
                    owner=uuid4(),
                    now=when,
                    lease_seconds=60,
                    tenant_id=tenant,
                    kind="activity.publish",
                )

        assert await claim(now) is None
        job = await claim(now + timedelta(seconds=11))
        assert job is not None
        assert (await dispatch_job(database, job, now + timedelta(seconds=11))).state == "pending"
        await database.engine.dispose()
        job2 = await claim(now + timedelta(seconds=15))
        assert (
            await dispatch_job(database, job2, now + timedelta(seconds=15))
        ).state == "succeeded"
        assert (await dispatch_job(database, job, now + timedelta(seconds=16))).state == "stale"
        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(Content).where(Content.tenant_id == tenant)
                )
                == 1
            )
        await client.post(f"/_mock/tenants/{tenant}/evidence/clock/advance", json={"seconds": 1})
        item = (
            await client.get(
                path + "/subscriptions/content?contentType=Audit.Exchange", headers=auth
            )
        ).json()[0]
        assert (await client.get(item["contentUri"], headers=auth)).json()[0]["Id"] == records[0][
            "id"
        ]
        await client.post(path + "/subscriptions/stop?contentType=Audit.Exchange", headers=auth)
        assert (await client.get(item["contentUri"], headers=auth)).status_code == 400
        await client.post(path + "/subscriptions/start?contentType=Audit.Exchange", headers=auth)
        assert (await client.get(item["contentUri"], headers=auth)).status_code == 400
        assert (
            await client.get(
                path + "/subscriptions/content?contentType=Audit.Exchange", headers=auth
            )
        ).json() == []


async def test_concurrent_publishers_share_idempotence_receipt(database, settings):
    import asyncio

    async with graph_client(database, settings) as (client, tenant, app, user):
        now = await setup(client, tenant)
        records = await seed(database, tenant, 2, when=now - timedelta(hours=1))

        async def publish():
            async with database.sessions.begin() as session:
                return await publish_activity(
                    session, tenant, [UUID(r["id"]) for r in records], now
                )

        left, right = await asyncio.gather(publish(), publish())
        assert left == right
        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(Content).where(Content.tenant_id == tenant)
                )
                == 2
            )

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_activity_publication import setup


def record(tenant, workload, record_type):
    return {
        "Id": str(uuid4()),
        "OrganizationId": str(tenant),
        "CreationTime": (datetime.now(UTC) - timedelta(days=20)).isoformat(),
        "Operation": "Observed",
        "RecordType": record_type,
        "UserType": 0,
        "UserKey": "actor",
        "UserId": "actor@example.test",
        "Workload": workload,
    }


async def test_all_content_types_dlp_and_publisher_quota(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await setup(client, tenant)
        path = f"/api/v1.0/{tenant}/activity/feed"
        admin = f"/_mock/tenants/{tenant}/activity"
        auth = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {"ActivityFeed.Read", "ActivityFeed.ReadDlp"},
            audience="https://manage.office.com",
        )
        rows = [
            ("Audit.AzureActiveDirectory", "AzureActiveDirectory", 8),
            ("Audit.Exchange", "Exchange", 1),
            ("Audit.SharePoint", "OneDrive", 7),
            ("Audit.General", "MicrosoftTeams", 25),
            ("DLP.All", "SharePoint", 11),
        ]
        for kind, workload, number in rows:
            assert (
                await client.post(path + "/subscriptions/start?contentType=" + kind, headers=auth)
            ).status_code == 200
            response = await client.post(
                admin + "/import", json={"records": [record(tenant, workload, number)]}
            )
            assert response.status_code == 200, response.text
        await client.post(f"/_mock/tenants/{tenant}/evidence/clock/advance", json={"seconds": 1})
        for kind, workload, number in rows:
            result = await client.get(
                path + "/subscriptions/content?contentType=" + kind, headers=auth
            )
            assert result.status_code == 200 and len(result.json()) == 1, result.text
            content = await client.get(result.json()[0]["contentUri"], headers=auth)
            assert content.json()[0]["RecordType"] == number
            if kind == "DLP.All":
                limited = await headers_for(
                    database,
                    settings,
                    tenant,
                    app,
                    None,
                    {"ActivityFeed.Read"},
                    audience="https://manage.office.com",
                )
                assert (
                    await client.get(result.json()[0]["contentUri"], headers=limited)
                ).status_code == 403
        await client.patch(admin + "/settings", json={"request_limit": 1})
        publisher = str(uuid4())
        url = path + "/subscriptions/list?PublisherIdentifier=" + publisher
        assert (await client.get(url, headers=auth)).status_code == 200
        import httpx

        from mockazure.admin.app import create_app

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings, database)),
            base_url=str(client.base_url),
            trust_env=False,
        ) as replica:
            denied = await replica.get(url, headers=auth)
        assert denied.status_code == 429 and denied.json()["error"]["code"] == "AF429"
        assert int(denied.headers["Retry-After"]) > 0
        assert (
            await client.get(
                path + "/subscriptions/list?PublisherIdentifier=" + str(uuid4()), headers=auth
            )
        ).status_code == 200


async def test_malformed_body_errors_and_expired_query_are_service_shaped(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await setup(client, tenant)
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
        result = await client.post(
            path + "/subscriptions/start?contentType=Audit.Exchange",
            headers=auth,
            content=b"{broken",
        )
        assert result.status_code == 400 and result.json()["error"]["code"].startswith("AF"), (
            result.text
        )
        await client.post(path + "/subscriptions/start?contentType=Audit.Exchange", headers=auth)
        await client.post(
            f"/_mock/tenants/{tenant}/activity/import",
            json={"records": [record(tenant, "Exchange", 1) for _ in range(3)]},
        )
        await client.post(f"/_mock/tenants/{tenant}/evidence/clock/advance", json={"seconds": 1})
        first = await client.get(
            path + "/subscriptions/content?contentType=Audit.Exchange", headers=auth
        )
        await client.post(
            f"/_mock/tenants/{tenant}/evidence/clock/advance", json={"seconds": 8 * 86400}
        )
        expired = await client.get(first.headers["NextPageUri"], headers=auth)
        assert expired.status_code == 400 and expired.json()["error"]["code"].startswith("AF"), (
            expired.text
        )


async def test_immutable_identity_atomic_import_and_reset_fences_worker(database, settings):
    from mockazure.jobs.dispatch import dispatch_job
    from mockazure.jobs.repository import claim_job
    from tests.integration.test_audit_queries import seed

    async with graph_client(database, settings) as (client, tenant, app, user):
        now = await setup(client, tenant)
        admin = f"/_mock/tenants/{tenant}/activity"
        original = record(tenant, "Exchange", 1)
        first = await client.post(admin + "/import", json={"records": [original]})
        assert first.status_code == 200
        assert (
            await client.post(admin + "/import", json={"records": [original]})
        ).json() == first.json()
        changed = {**original, "Operation": "Changed"}
        assert (
            await client.post(admin + "/import", json={"records": [changed]})
        ).status_code == 422
        assert (
            await client.post(
                admin + "/import",
                json={"records": [record(tenant, "Exchange", 1), record(uuid4(), "Exchange", 1)]},
            )
        ).status_code == 422
        assert len((await client.get(admin + "/export")).json()["contents"]) == 1
        await seed(database, tenant, 1)
        async with database.sessions.begin() as session:
            claim = await claim_job(
                session,
                owner=uuid4(),
                now=now + timedelta(seconds=5),
                lease_seconds=60,
                tenant_id=tenant,
                kind="activity.publish",
            )
        assert claim is not None
        generation = (await client.get(admin + "/state")).json()["generation"]
        assert (
            await client.post(admin + "/reset", json={"expected_generation": generation})
        ).status_code == 200
        assert (await dispatch_job(database, claim, now + timedelta(seconds=6))).state == "stale"
        assert (await client.get(admin + "/export")).json()["contents"] == []

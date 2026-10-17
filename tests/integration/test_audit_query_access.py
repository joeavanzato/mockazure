from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from mockazure.db.job_model import Job
from tests.integration.graph_support import graph_client, headers_for, registered_app
from tests.integration.test_audit_queries import PATH, claim, entitlement, finish, query_body, seed


async def test_audit_page_current_role_and_tenant_isolation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await entitlement(client, tenant)
        await seed(database, tenant)
        headers = await headers_for(
            database, settings, tenant, app, None, {"AuditLogsQuery.Read.All"}
        )
        response = await client.post(
            PATH, headers=headers, json=query_body(serviceFilter="Exchange")
        )
        assert response.status_code == 201
        item = PATH + "/" + response.json()["id"]
        await finish(database, tenant)
        delegated = await headers_for(
            database, settings, tenant, app, user, {"AuditLogsQuery-Exchange.Read.All"}, "delegated"
        )
        role = f"/_mock/tenants/{tenant}/mail/audit-roles/{user}/Audit Logs"
        assert (await client.put(role)).status_code == 200
        page = await client.get(item + "/records?$top=1", headers=delegated)
        assert page.status_code == 200
        link = page.json()["@odata.nextLink"]
        assert (await client.delete(role)).status_code == 204
        assert (await client.get(link, headers=delegated)).status_code == 403
        other_tenant = (
            await client.post(
                "/_mock/tenants",
                json={
                    "slug": "other",
                    "display_name": "Other",
                    "domain": "other.test",
                    "licences": ["E5"],
                },
            )
        ).json()["id"]
        other_app = await registered_app(client, other_tenant)
        await entitlement(client, other_tenant)
        foreign = await headers_for(
            database, settings, other_tenant, other_app, None, {"AuditLogsQuery.Read.All"}
        )
        assert (await client.get(item, headers=foreign)).status_code == 404
        assert (await client.get(item + "/records", headers=foreign)).status_code == 404
        assert (await client.get(PATH, headers=foreign)).json()["value"] == []
        assert (await client.get(link, headers=foreign)).status_code == 404
        assert (
            await client.put(f"/_mock/tenants/{other_tenant}/mail/audit-roles/{user}/Audit Logs")
        ).status_code == 404


async def test_audit_query_list_pages_and_exhausted_lease(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await entitlement(client, tenant)
        headers = await headers_for(
            database, settings, tenant, app, None, {"AuditLogsQuery.Read.All"}
        )
        created = []
        for _ in range(3):
            result = await client.post(PATH, headers=headers, json=query_body())
            assert result.status_code == 201
            created.append(result.json()["id"])
        first = (await client.get(PATH + "?$top=1", headers=headers)).json()
        later = await client.post(PATH, headers=headers, json=query_body())
        assert later.status_code == 201
        items = first["value"]
        link = first["@odata.nextLink"]
        while link:
            response = await client.get(link, headers=headers)
            assert response.status_code == 200
            page = response.json()
            items.extend(page["value"])
            link = page.get("@odata.nextLink")
        assert [row["id"] for row in items] == created
        # Force three genuine lease acquisitions to expire; no worker completion.
        now = datetime.now(UTC)
        original = await claim(database, tenant, now, lease=1)
        async with database.sessions.begin() as session:
            others = list(
                await session.scalars(
                    select(Job).where(
                        Job.tenant_id == tenant,
                        Job.kind == "audit.query",
                        Job.id != original.id,
                    )
                )
            )
            for row in others:
                row.available_at = now + timedelta(days=1)
        assert (
            await claim(database, tenant, now + timedelta(seconds=2), lease=1)
        ).id == original.id
        assert (
            await claim(database, tenant, now + timedelta(seconds=4), lease=1)
        ).id == original.id
        assert await claim(database, tenant, now + timedelta(seconds=6), lease=1) is None
        item = PATH + "/" + original.payload["query_id"]
        assert (await client.get(item, headers=headers)).json()["status"] == "failed"
        assert (await client.get(item + "/records", headers=headers)).status_code == 409
        assert UUID(original.payload["query_id"])

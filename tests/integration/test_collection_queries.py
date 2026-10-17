from sqlalchemy import update

from mockazure.identity.models import DirectoryRole
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_collection_evidence import seed


async def test_filtered_ordered_projection_null_paging_and_atomic_replace(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        rows = [
            {"id": "a", "severity": "high", "createdDateTime": "2026-10-01T00:00:00.1234567Z"},
            {"id": "b", "severity": "low", "createdDateTime": "2026-10-01T00:00:00.1234568Z"},
            {"id": "c", "severity": "high"},
            {"id": "d", "severity": "high"},
        ]
        await seed(client, tenant, "alerts", rows)
        h = await headers_for(database, settings, tenant, app, None, {"SecurityEvents.Read.All"})
        api = "/v1.0/security/alerts"
        first = await client.get(
            api,
            headers=h,
            params={
                "$filter": "severity eq 'high'",
                "$select": "id, severity",
                "$orderby": "createdDateTime desc",
                "$top": "1",
                "$count": "true",
            },
        )
        assert first.status_code == 200, first.text
        assert (
            first.json()["value"] == [{"id": "a", "severity": "high"}]
            and first.json()["@odata.count"] == 3
        )
        link = first.json()["@odata.nextLink"]
        rejected = await client.post(
            f"/_mock/tenants/{tenant}/collections/alerts/import",
            json={"records": [{"id": "c", "severity": "low"}, {"id": "bad", "severity": False}]},
        )
        assert rejected.status_code == 422
        seen = ["a"]
        while link:
            page = await client.get(link, headers=h)
            assert page.status_code == 200, page.text
            seen.extend(p["id"] for p in page.json()["value"])
            link = page.json().get("@odata.nextLink")
        assert seen == ["a", "c", "d"]
        assert (await client.get(api + "/c", headers=h)).json()["severity"] == "high"
        assert (
            await client.get(first.json()["@odata.nextLink"] + "&$top=5", headers=h)
        ).status_code == 400
        assert (
            await client.get(
                first.json()["@odata.nextLink"].replace("/alerts?", "/secureScores?"), headers=h
            )
        ).status_code == 400


async def test_secure_score_decimal_filter_and_current_role_cursor_binding(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed(
            client,
            tenant,
            "secureScores",
            [{"id": "a", "currentScore": 12.5}, {"id": "b", "currentScore": 11.0}],
        )
        h = await headers_for(database, settings, tenant, app, None, {"SecurityEvents.Read.All"})
        response = await client.get(
            "/v1.0/security/secureScores", headers=h, params={"$filter": "currentScore ge 12.5"}
        )
        assert response.status_code == 200, response.text
        assert [p["id"] for p in response.json()["value"]] == ["a"]
        await seed(client, tenant, "incidents", [{"id": "a"}, {"id": "b"}])
        async with database.sessions.begin() as session:
            session.add(DirectoryRole(tenant_id=tenant, user_id=user, role="Security Reader"))
        delegated = await headers_for(
            database, settings, tenant, app, user, {"SecurityIncident.Read.All"}, "delegated"
        )
        page = await client.get("/v1.0/security/incidents", headers=delegated, params={"$top": "1"})
        assert page.status_code == 200, page.text
        async with database.sessions.begin() as session:
            await session.execute(
                update(DirectoryRole)
                .where(DirectoryRole.tenant_id == tenant, DirectoryRole.user_id == user)
                .values(role="Security Administrator")
            )
        assert (
            await client.get(page.json()["@odata.nextLink"], headers=delegated)
        ).status_code == 400
        assert (await client.get("/v1.0/security/incidents", headers=delegated)).status_code == 200

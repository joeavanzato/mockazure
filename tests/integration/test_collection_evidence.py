from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, update

from mockazure.admin.app import create_app
from mockazure.identity.models import Application, DirectoryRole
from tests.integration.graph_support import graph_client, headers_for, registered_app

FAMILIES = [
    ("alerts", "SecurityEvents.Read.All", {"status": "newAlert", "severity": "high"}),
    ("alerts_v2", "SecurityAlert.Read.All", {"status": "new", "severity": "high"}),
    ("incidents", "SecurityIncident.Read.All", {"status": "active", "severity": "high"}),
    ("secureScores", "SecurityEvents.Read.All", {"currentScore": 12.5, "maxScore": 50.0}),
    ("secureScoreControlProfiles", "SecurityEvents.Read.All", {"title": "MFA", "maxScore": 10.0}),
]


async def seed(client, tenant, family, records):
    result = await client.post(
        f"/_mock/tenants/{tenant}/collections/{family}/import", json={"records": records}
    )
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.parametrize(("family", "permission", "fields"), FAMILIES)
async def test_import_list_get_and_permissions(database, settings, family, permission, fields):
    async with graph_client(database, settings) as (client, tenant, app, user):
        rows = [{"id": f"record-{i}", **fields} for i in range(3)]
        imported = await seed(client, tenant, family, rows)
        assert imported["count"] == 3 and imported["revision"] == 1
        path = "/v1.0/security/" + family
        wrong = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        assert (await client.get(path, headers=wrong)).status_code == 403
        headers = await headers_for(database, settings, tenant, app, None, {permission})
        result = await client.get(path, headers=headers, params={"$top": "1", "$count": "true"})
        assert result.status_code == 200, result.text
        assert result.json()["@odata.count"] == 3
        seen = result.json()["value"]
        while "@odata.nextLink" in result.json():
            result = await client.get(result.json()["@odata.nextLink"], headers=headers)
            assert result.status_code == 200, result.text
            seen += result.json()["value"]
        assert {r["id"] for r in seen} == {r["id"] for r in rows}
        assert (await client.get(path + "/record-1", headers=headers)).json()["id"] == "record-1"
        assert (await client.get(path + "/missing", headers=headers)).status_code == 404


async def test_snapshot_restart_mutation_cursor_binding_and_expiry(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        rows = [
            {"id": str(i), "createdDateTime": f"2026-10-01T00:00:0{i}.1234567Z", "severity": "high"}
            for i in range(3)
        ]
        await seed(client, tenant, "alerts_v2", rows)
        headers = await headers_for(
            database, settings, tenant, app, None, {"SecurityAlert.Read.All"}
        )
        response = await client.get(
            "/v1.0/security/alerts_v2", headers=headers, params={"$top": "1"}
        )
        assert response.status_code == 200, response.text
        link = response.json()["@odata.nextLink"]
        await seed(
            client,
            tenant,
            "alerts_v2",
            [{"id": "1", "severity": "low"}, {"id": "new", "severity": "medium"}],
        )
        other = await registered_app(client, tenant)
        other_headers = await headers_for(
            database, settings, tenant, other, None, {"SecurityAlert.Read.All"}
        )
        assert (await client.get(link, headers=other_headers)).status_code == 400
        changed = await headers_for(
            database, settings, tenant, app, None, {"SecurityAlert.Read.All", "User.Read.All"}
        )
        assert (await client.get(link, headers=changed)).status_code == 400
        fresh = create_app(settings, database)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fresh), base_url="https://localhost:8443"
        ) as restarted:
            seen = response.json()["value"]
            while link:
                page = await restarted.get(link, headers=headers)
                assert page.status_code == 200, page.text
                seen += page.json()["value"]
                link = page.json().get("@odata.nextLink")
            assert {r["id"] for r in seen} == {"0", "1", "2"}
            assert all(r["severity"] == "high" for r in seen)
            fresh.state.clock = lambda: datetime.now(UTC) + timedelta(hours=2)
            assert (
                await restarted.get(response.json()["@odata.nextLink"], headers=headers)
            ).status_code == 400
        async with database.sessions.begin() as session:
            await session.execute(
                update(Application).where(Application.client_id == app).values(enabled=False)
            )
        assert (
            await client.get(response.json()["@odata.nextLink"], headers=headers)
        ).status_code == 403


async def test_invalid_batch_atomicity_query_types_precision_and_isolation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path = f"/_mock/tenants/{tenant}/collections/alerts_v2/import"
        invalid = [
            [{"id": "valid"}, {"id": "bad", "severity": "pretend"}],
            [{"id": "same"}, {"id": "same"}],
            [{"id": "foreign", "tenantId": str(uuid4())}],
            [{"id": "../bad"}],
        ]
        for records in invalid:
            response = await client.post(path, json={"records": records})
            assert response.status_code == 422, response.text
        headers = await headers_for(
            database, settings, tenant, app, None, {"SecurityAlert.Read.All"}
        )
        api = "/v1.0/security/alerts_v2"
        assert (await client.get(api, headers=headers)).json()["value"] == []
        await seed(
            client,
            tenant,
            "alerts_v2",
            [
                {
                    "id": "first",
                    "createdDateTime": "2026-10-01T00:00:00.1234567Z",
                    "severity": "high",
                },
                {
                    "id": "second",
                    "createdDateTime": "2026-10-01T00:00:00.1234568Z",
                    "severity": "low",
                },
            ],
        )
        exact = await client.get(
            api,
            headers=headers,
            params={"$filter": "createdDateTime eq 2026-10-01T02:00:00.1234567+02:00"},
        )
        assert exact.status_code == 200, exact.text
        assert [r["id"] for r in exact.json()["value"]] == ["first"]
        for query in [
            {"$filter": "unknown eq 'x'"},
            {"$filter": "createdDateTime eq 3"},
            {"$expand": "anything"},
            {"$orderby": "createdDateTime"},
            {"$count": "TRUE"},
            {"$skip": "-1"},
            {"$top": "0"},
        ]:
            assert (await client.get(api, headers=headers, params=query)).status_code == 400
        other = (
            await client.post(
                "/_mock/tenants",
                json={
                    "slug": "other",
                    "domain": "other.test",
                    "display_name": "Other",
                    "licences": [],
                },
            )
        ).json()["id"]
        other_app = await registered_app(client, other)
        other_headers = await headers_for(
            database, settings, other, other_app, None, {"SecurityAlert.Read.All"}
        )
        assert (await client.get(api, headers=other_headers)).json()["value"] == []
        assert (await client.get(api + "/first", headers=other_headers)).status_code == 404


async def test_delegated_role_revocation_and_incident_expansion(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed(
            client,
            tenant,
            "incidents",
            [{"id": "10", "severity": "high"}, {"id": "11", "severity": "low"}],
        )
        await seed(
            client, tenant, "alerts_v2", [{"id": "a", "incidentId": "10", "title": "Evidence"}]
        )
        headers = await headers_for(
            database, settings, tenant, app, user, {"SecurityIncident.Read.All"}, "delegated"
        )
        api = "/v1.0/security/incidents"
        assert (await client.get(api, headers=headers)).status_code == 403
        async with database.sessions.begin() as session:
            session.add(DirectoryRole(tenant_id=tenant, user_id=user, role="Security Reader"))
        response = await client.get(api, headers=headers, params={"$top": "1", "$expand": "alerts"})
        assert response.status_code == 200, response.text
        assert response.json()["value"][0]["alerts"][0]["id"] == "a"
        link = response.json()["@odata.nextLink"]
        async with database.sessions.begin() as session:
            await session.execute(
                delete(DirectoryRole).where(
                    DirectoryRole.tenant_id == tenant, DirectoryRole.user_id == user
                )
            )
        assert (await client.get(link, headers=headers)).status_code == 403

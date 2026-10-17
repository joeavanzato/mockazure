from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_graph_evidence_reads import publish, record
from tests.integration.test_graph_licensing import P2, seed_sku, sku_input


async def test_activity_snapshot_survives_new_signins_and_checks_permissions(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant)
        other = await client.post(
            f"/_mock/tenants/{tenant}/identity/users",
            json={
                "upn": "second@contoso.test",
                "display_name": "Second",
                "password": "local-test-password",
            },
        )
        assert other.status_code == 201
        auth = await headers_for(
            database, settings, tenant, app, None, {"User.Read.All", "AuditLog.Read.All"}
        )
        first = await client.get(
            "/v1.0/users", headers=auth, params={"$select": "id,signInActivity", "$top": 1}
        )
        assert first.status_code == 200, first.text
        link = first.json()["@odata.nextLink"]
        remaining = ({str(user), other.json()["id"]} - {first.json()["value"][0]["id"]}).pop()
        now = datetime.now(UTC) - timedelta(minutes=1)
        value = record("signIn", UUID(remaining), now)
        await publish(database, tenant, "signIn", value, now)
        page = await client.get(link, headers=auth)
        assert page.status_code == 200, page.text
        assert page.json()["value"][0]["signInActivity"]["lastSignInRequestId"] is None
        current = await client.get(
            "/v1.0/users/" + remaining, headers=auth, params={"$select": "signInActivity"}
        )
        assert current.json()["signInActivity"]["lastSignInRequestId"] == value["id"]
        assert (await client.get(link, headers=auth)).json() == page.json()
        denied = await headers_for(database, settings, tenant, app, None, {"User.Read.All"})
        assert (await client.get(link, headers=denied)).status_code == 403
        assert (
            await client.patch(
                "/v1.0/users/" + remaining,
                headers=auth,
                json={"signInActivity": {"lastSignInRequestId": "fake"}},
            )
        ).status_code in {400, 403}


async def test_activity_respects_availability_and_older_replay(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant)
        now = datetime(2026, 10, 17, tzinfo=UTC)
        control = f"/_mock/tenants/{tenant}/evidence/settings"
        assert (
            await client.patch(control, json={"clock_mode": "fixed", "fixed_time": now.isoformat()})
        ).status_code == 200
        auth = await headers_for(
            database, settings, tenant, app, None, {"User.Read.All", "AuditLog.Read.All"}
        )
        old = record("signIn", user, now - timedelta(minutes=5))
        future = record("signIn", user, now - timedelta(minutes=1))
        await publish(database, tenant, "signIn", old, now - timedelta(minutes=5))
        await publish(
            database,
            tenant,
            "signIn",
            future,
            now - timedelta(minutes=1),
            available=now + timedelta(minutes=1),
        )
        path = "/v1.0/users/" + str(user)
        result = await client.get(path, headers=auth, params={"$select": "signInActivity"})
        assert result.json()["signInActivity"]["lastSignInRequestId"] == old["id"]
        assert (
            await client.patch(
                control,
                json={
                    "clock_mode": "fixed",
                    "fixed_time": (now + timedelta(minutes=2)).isoformat(),
                },
            )
        ).status_code == 200
        result = await client.get(path, headers=auth, params={"$select": "signInActivity"})
        assert result.json()["signInActivity"]["lastSignInRequestId"] == future["id"]


async def test_registration_cursor_licence_and_cross_tenant_boundaries(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant)
        auth = await headers_for(database, settings, tenant, app, None, {"AuditLog.Read.All"})
        users = [
            {
                "id": str(uuid4()),
                "displayName": "Report user " + str(i),
                "userPrincipalName": f"report{i}@contoso.test",
                "mailNickname": f"report{i}",
                "accountEnabled": True,
            }
            for i in range(101)
        ]
        result = await client.post(
            f"/_mock/tenants/{tenant}/directory/import",
            json={
                "schema_version": 1,
                "users": users,
                "groups": [],
                "relationships": [],
                "provenance": {"source": "registration-paging-test"},
            },
        )
        assert result.status_code == 201, result.text
        path = "/v1.0/reports/authenticationMethods/userRegistrationDetails"
        first = await client.get(path, headers=auth)
        assert first.status_code == 200
        link = first.json()["@odata.nextLink"]
        second = await client.get(link, headers=auth)
        assert second.status_code == 200
        rows = first.json()["value"] + second.json()["value"]
        assert len(rows) == 102 and len({v["id"] for v in rows}) == 102
        assert (await client.get(link, headers=auth)).json() == second.json()
        assert (
            await client.get(link + "&$filter=isMfaRegistered%20eq%20true", headers=auth)
        ).status_code == 400
        foreign = await client.post(
            "/_mock/tenants",
            json={
                "slug": "foreign-report",
                "display_name": "Foreign",
                "domain": "foreign-report.test",
                "licences": [],
            },
        )
        foreign_app = await client.post(
            f"/_mock/tenants/{foreign.json()['id']}/identity/applications",
            json={"display_name": "Foreign app", "client_mode": "public"},
        )
        foreign_auth = await headers_for(
            database,
            settings,
            foreign.json()["id"],
            foreign_app.json()["client_id"],
            None,
            {"AuditLog.Read.All"},
        )
        await seed_sku(client, foreign.json()["id"])
        assert (await client.get(path + "/" + str(user), headers=foreign_auth)).status_code == 404
        assert (await client.get(link, headers=foreign_auth)).status_code == 400

        disabled = sku_input()
        disabled["status"] = "Suspended"
        assert (
            await client.put(f"/_mock/tenants/{tenant}/licensing/skus/{P2}", json=disabled)
        ).status_code == 200
        assert (await client.get(link, headers=auth)).status_code == 403

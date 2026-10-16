from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from identity_support import identity_client

pytestmark = pytest.mark.asyncio


async def test_graph_application_permissions_projection_and_error_shapes(database, settings):
    async with identity_client(database, settings) as (client, tenant, app, secret, _, _):

        async def token(scope):
            result = await client.post(
                f"/{tenant}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": app,
                    "client_secret": secret["secret"],
                    "scope": scope,
                },
            )
            assert result.status_code == 200, result.text
            return {"Authorization": "Bearer " + result.json()["access_token"]}

        headers = await token("https://graph.microsoft.com/.default")
        response = await client.get("/v1.0/organization", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["value"][0]["id"] == tenant
        assert response.json()["value"][0]["verifiedDomains"][0]["name"] == "contoso.test"
        selected = await client.get("/beta/organization?$select=id", headers=headers)
        assert selected.json()["value"] == [{"id": tenant}]
        assert (
            await client.get("/v1.0/organization?$filter=id eq 'anything'", headers=headers)
        ).status_code == 400
        invalid = await client.get("/v1.0/organization", headers={"Authorization": "Bearer bad"})
        assert invalid.status_code == 401
        assert invalid.json()["error"]["code"] == "InvalidAuthenticationToken"
        assert "innerError" in invalid.json()["error"]
        assert (await client.get("/v1.0/me", headers=headers)).status_code == 400
        exchange = await token("https://outlook.office365.com/.default")
        assert (await client.get("/v1.0/organization", headers=exchange)).status_code == 401
        # No application permission in this second tenant; token issuance is still possible.
        second = (
            await client.post(
                "/_mock/tenants",
                json={"slug": "other", "display_name": "Other", "domain": "other.test"},
            )
        ).json()["id"]
        from mockazure.identity.keys import SigningKeyStore, read_wrapping_key
        from mockazure.identity.tokens import issue_access_token
        from mockazure.identity.types import PrincipalContext

        async with database.sessions() as session:
            keys = SigningKeyStore(
                session, read_wrapping_key(settings.data_dir), settings.endpoints
            )
            raw = await issue_access_token(
                PrincipalContext(
                    UUID(second),
                    UUID(app),
                    uuid4(),
                    "application",
                    "00000003-0000-0000-c000-000000000000",
                    frozenset(),
                ),
                datetime.now(UTC),
                keys,
            )
        denied = await client.get("/v1.0/organization", headers={"Authorization": "Bearer " + raw})
        assert denied.status_code == 403
        assert tenant not in denied.text


async def test_graph_delegated_access_uses_current_user_and_directory_roles(database, settings):
    from mockazure.identity.keys import SigningKeyStore, read_wrapping_key
    from mockazure.identity.tokens import issue_access_token
    from mockazure.identity.types import PrincipalContext

    async with identity_client(database, settings) as (client, tenant, app, _, _, _):
        base = f"/_mock/tenants/{tenant}/identity"
        user = (
            await client.post(
                base + "/users",
                json={
                    "upn": "analyst@contoso.test",
                    "display_name": "Analyst",
                    "password": "local-password-123",
                },
            )
        ).json()["id"]

        async def headers(permissions):
            async with database.sessions() as session:
                store = SigningKeyStore(
                    session, read_wrapping_key(settings.data_dir), settings.endpoints
                )
                raw = await issue_access_token(
                    PrincipalContext(
                        UUID(tenant),
                        UUID(app),
                        UUID(user),
                        "delegated",
                        "00000003-0000-0000-c000-000000000000",
                        frozenset(permissions),
                    ),
                    datetime.now(UTC),
                    store,
                )
            return {"Authorization": "Bearer " + raw}

        basic = await headers(["User.Read"])
        me = await client.get("/v1.0/me", headers=basic)
        assert me.status_code == 200, me.text
        assert me.json()["id"] == user and me.json()["userPrincipalName"] == "analyst@contoso.test"
        assert "password" not in me.text and "verifier" not in me.text
        assert (await client.get("/v1.0/organization", headers=basic)).status_code == 200
        broader = await headers(["Organization.Read.All"])
        assert (await client.get("/v1.0/organization", headers=broader)).status_code == 403
        assert (await client.put(base + f"/users/{user}/roles/Global Reader")).status_code == 200
        assert (await client.get("/v1.0/organization", headers=broader)).status_code == 200
        await client.patch(base + f"/users/{user}", json={"enabled": False})
        assert (await client.get("/v1.0/me", headers=basic)).status_code == 403

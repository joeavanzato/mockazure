from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from mockazure.identity.models import Application, ServicePrincipal
from mockazure.managed_identity.app import create_managed_identity_app
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_managed_identity import arguments, grant, host, identity


@pytest.mark.parametrize("resource", ["applications", "servicePrincipals"])
async def test_managed_identity_cannot_become_a_secret_client(database, settings, resource):
    async with graph_client(database, settings) as (client, tenant, actor, _):
        managed = await identity(client, tenant)
        await grant(client, tenant, managed["client_id"], ["Organization.Read.All"])
        binding = await host(client, tenant, users=[managed["id"]])
        headers = await headers_for(
            database, settings, tenant, actor, None, {"Application.ReadWrite.All"}
        )
        path = f"/v1.0/{resource}(appId='{managed['client_id']}')"
        if resource == "applications":
            for platform in ("web", "publicClient", "spa"):
                response = await client.patch(
                    path, headers=headers, json={platform: {"redirectUris": []}}
                )
                assert response.status_code == 400, response.text
        for body in ({"keyCredentials": []},):
            assert (await client.patch(path, headers=headers, json=body)).status_code == 400
        for action in ("addPassword", "microsoft.graph.addPassword"):
            response = await client.post(
                path + "/" + action, headers=headers, json={"passwordCredential": {}}
            )
            assert response.status_code == 400, response.text
        response = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": managed["client_id"],
                "client_secret": "must-never-authenticate",
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        assert response.status_code in (400, 401)
        async with database.sessions() as session:
            app = await session.get(Application, UUID(managed["client_id"]))
            assert app.client_mode == "managed"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_managed_identity_app(settings, database)),
            base_url="http://127.0.0.1:49446",
            headers={"X-IDENTITY-HEADER": binding["header"]},
        ) as ingress:
            result = await ingress.get(
                "/MSI/token", params=arguments(client_id=managed["client_id"])
            )
            assert result.status_code == 200, result.text


@pytest.mark.parametrize("resource", ["applications", "servicePrincipals"])
async def test_graph_deleted_managed_identity_cannot_be_reenabled(database, settings, resource):
    async with graph_client(database, settings) as (client, tenant, actor, _):
        managed = await identity(client, tenant)
        binding = await host(client, tenant, users=[managed["id"]])
        headers = await headers_for(
            database, settings, tenant, actor, None, {"Application.ReadWrite.All"}
        )
        path = f"/v1.0/{resource}(appId='{managed['client_id']}')"
        assert (await client.delete(path, headers=headers)).status_code == 204
        result = await client.patch(
            f"/_mock/tenants/{tenant}/managed-identities/{managed['id']}", json={"enabled": True}
        )
        assert result.status_code == 409, result.text
        assert (await client.get(path, headers=headers)).status_code == 404
        # Cover preexisting inconsistent tombstoned rows independently of enable validation.
        async with database.sessions.begin() as session:
            app = await session.get(Application, UUID(managed["client_id"]))
            sp = await session.get(ServicePrincipal, UUID(managed["id"]))
            app.enabled = sp.enabled = True
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_managed_identity_app(settings, database)),
            base_url="http://127.0.0.1:49446",
            headers={"X-IDENTITY-HEADER": binding["header"]},
        ) as ingress:
            result = await ingress.get(
                "/MSI/token", params=arguments(client_id=managed["client_id"])
            )
            assert result.status_code == 403, result.text


@pytest.mark.parametrize("kind", ["application", "servicePrincipal"])
async def test_legacy_enabled_tombstone_cannot_issue_managed_token(database, settings, kind):
    async with graph_client(database, settings) as (client, tenant, actor, _):
        managed = await identity(client, tenant)
        binding = await host(client, tenant, users=[managed["id"]])
        async with database.sessions.begin() as session:
            model, key = (
                (Application, managed["client_id"])
                if kind == "application"
                else (ServicePrincipal, managed["id"])
            )
            row = await session.get(model, UUID(key))
            row.deleted_at = datetime.now(UTC)
            assert row.enabled
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_managed_identity_app(settings, database)),
            base_url="http://127.0.0.1:49446",
            headers={"X-IDENTITY-HEADER": binding["header"]},
        ) as ingress:
            result = await ingress.get(
                "/MSI/token", params=arguments(client_id=managed["client_id"])
            )
            assert result.status_code == 403, result.text

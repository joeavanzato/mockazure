from uuid import UUID

import pytest
from pydantic import SecretStr

from mockazure.admin.errors import AdminError
from mockazure.population.transport import ClientCredential, LocalTransport
from mockazure.population.types import AdapterContext
from tests.integration.identity_support import identity_client


async def test_public_actions_use_real_local_oauth_and_enforce_permissions(database, settings):
    async with identity_client(database, settings) as (client, tenant, app, secret, *_):
        ctx = AdapterContext(database, settings, UUID(tenant))
        credentials = ClientCredential(UUID(app), SecretStr(secret["secret"]))
        transport = LocalTransport(ctx, credentials, client=client)
        response = await transport.request("graph", "GET", "/v1.0/organization")
        assert response.status_code == 200, response.text
        denied = await transport.request("graph", "GET", "/v1.0/users")
        assert denied.status_code == 403
        bad = LocalTransport(ctx, ClientCredential(UUID(app), SecretStr("wrong")), client=client)
        with pytest.raises(AdminError) as error:
            await bad.request("graph", "GET", "/v1.0/organization")
        assert error.value.status == 401


async def test_rejects_foreign_endpoints_and_redirects(database, settings):
    import httpx

    async with identity_client(database, settings) as (client, tenant, app, secret, *_):
        ctx = AdapterContext(database, settings, UUID(tenant))
        credential = ClientCredential(UUID(app), SecretStr(secret["secret"]))
        transport = LocalTransport(ctx, credential, client=client)
        for path in (
            "https://graph.microsoft.com/v1.0/users",
            "//other.test/",
            "/_mock/tenants",
            "\\other",
        ):
            with pytest.raises(ValueError):
                await transport.request("graph", "GET", path)
        calls = []

        def redirected(request):
            calls.append(str(request.url))
            return httpx.Response(302, headers={"Location": "https://graph.microsoft.com/"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(redirected)) as redirect_client:
            transport = LocalTransport(ctx, credential, client=redirect_client)
            with pytest.raises(AdminError):
                await transport.request("graph", "GET", "/v1.0/organization")
            assert len(calls) == 1
            assert calls[0].startswith("https://localhost:")

from tests.integration.identity_support import identity_client


async def test_provider_and_oauth_global_error_envelopes(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        response = await client.get("/v1.0/not-implemented")
        assert response.status_code == 501
        assert "innerError" in response.json()["error"]
        response = await client.get(f"/{tenant}/oauth2/v2.0/token")
        assert response.status_code == 405
        assert response.json()["error"] == "invalid_request"
        response = await client.post(f"/adminapi/v2.0/{tenant}/InvokeCommand", json={})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "InvalidRequest"


async def test_capabilities_describe_supported_identity_and_probes(database, settings):
    async with identity_client(database, settings) as (client, *_):
        response = (await client.get("/_mock/capabilities")).json()
        assert response["milestone"] == 2
        assert set(response["authentication_flows"]) == {
            "client_credentials",
            "authorization_code_pkce",
            "device_code",
            "refresh_token",
        }
        assert "graph.organization.read" in response["provider_operations"]
        assert "graph.auditLogs.signIns" not in response["provider_operations"]

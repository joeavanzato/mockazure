from datetime import UTC, datetime

import pytest

from tests.integration.graph_support import graph_client, headers_for


@pytest.mark.parametrize("mode,platform", [("public", "publicClient"), ("confidential", "web")])
async def test_bootstrap_redirects_are_visible_and_survive_graph_updates(
    database, settings, mode, platform
):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        uri = "http://localhost:7432/callback"
        response = await client.post(
            f"/_mock/tenants/{tenant}/identity/applications",
            json={
                "display_name": "Bootstrap platform",
                "client_mode": mode,
                "redirect_uris": [uri],
            },
        )
        assert response.status_code == 201
        app = response.json()["client_id"]
        headers = await headers_for(
            database, settings, tenant, actor, None, {"Application.ReadWrite.All"}
        )
        path = f"/v1.0/applications(appId='{app}')"
        data = (await client.get(path, headers=headers)).json()
        assert data.get(platform, {}).get("redirectUris") == [uri]
        response = await client.patch(
            path,
            headers=headers,
            json={"spa": {"redirectUris": ["http://localhost:7433/callback"]}},
        )
        assert response.status_code == 204, response.text
        administrative = (await client.get(f"/_mock/tenants/{tenant}/identity/applications")).json()
        stored = next(x for x in administrative["items"] if x["client_id"] == app)
        assert set(stored["redirect_uris"]) == {uri, "http://localhost:7433/callback"}
        assert stored["client_mode"] == mode


async def test_admin_secret_metadata_has_actual_creation_time(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        start = datetime.now(UTC)
        result = await client.post(
            f"/_mock/tenants/{tenant}/identity/applications/{actor}/credentials",
            json={"kind": "secret"},
        )
        assert result.status_code == 201, result.text
        headers = await headers_for(
            database, settings, tenant, actor, None, {"Application.Read.All"}
        )
        response = await client.get(f"/v1.0/applications(appId='{actor}')", headers=headers)
        value = next(
            x for x in response.json()["passwordCredentials"] if x["keyId"] == result.json()["id"]
        )
        assert datetime.fromisoformat(value["startDateTime"]) >= start

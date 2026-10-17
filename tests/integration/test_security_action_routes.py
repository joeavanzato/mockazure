import pytest

from tests.integration.graph_support import graph_client, headers_for


@pytest.mark.parametrize("resource", ["applications", "servicePrincipals"])
async def test_qualified_password_actions_share_state_and_permissions(database, settings, resource):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(
            database, settings, tenant, app, None, {"Application.ReadWrite.All"}
        )
        application = await client.post(
            "/v1.0/applications", headers=headers, json={"displayName": "Qualified action"}
        )
        assert application.status_code == 201
        obj = application.json()
        if resource == "servicePrincipals":
            result = await client.post(
                "/v1.0/servicePrincipals", headers=headers, json={"appId": obj["appId"]}
            )
            assert result.status_code == 201
            obj = result.json()
        path = "/v1.0/" + resource + "/" + obj["id"]
        added = await client.post(
            path + "/microsoft.graph.addPassword",
            headers=headers,
            json={"passwordCredential": {"displayName": "Qualified secret"}},
        )
        assert added.status_code == 200
        key = added.json()["keyId"]
        assert added.json()["secretText"]
        metadata = (await client.get(path, headers=headers)).json()["passwordCredentials"]
        assert any(row["keyId"] == key and row.get("secretText") is None for row in metadata)
        removed = await client.post(
            path + "/microsoft.graph.removePassword", headers=headers, json={"keyId": key}
        )
        assert removed.status_code == 204
        assert not any(
            row["keyId"] == key
            for row in (await client.get(path, headers=headers)).json()["passwordCredentials"]
        )
        denied = await headers_for(database, settings, tenant, app, None, {"Organization.Read.All"})
        result = await client.post(
            path + "/microsoft.graph.addPassword",
            headers=denied,
            json={"passwordCredential": {"displayName": "Denied"}},
        )
        assert result.status_code == 403

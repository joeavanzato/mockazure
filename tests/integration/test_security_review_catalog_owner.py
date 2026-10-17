from tests.integration.graph_support import graph_client, headers_for


async def test_live_resource_principal_owner_is_preserved(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, _):
        headers = await headers_for(
            database,
            settings,
            tenant,
            actor,
            None,
            {"Application.ReadWrite.All", "Directory.Read.All"},
        )
        result = await client.get(
            "/v1.0/servicePrincipals?$filter=appId eq '00000003-0000-0000-c000-000000000000'",
            headers=headers,
        )
        assert result.status_code == 200
        principal = result.json()["value"][0]["id"]
        created = await client.post(
            "/v1.0/applications", headers=headers, json={"displayName": "Resource owner target"}
        )
        path = "/v1.0/applications/" + created.json()["id"]
        assert (
            await client.post(
                path + "/owners/$ref",
                headers=headers,
                json={"@odata.id": "https://localhost:8443/v1.0/directoryObjects/" + principal},
            )
        ).status_code == 204
        result = await client.get(path + "/owners", headers=headers)
        assert result.status_code == 200
        assert principal in {value["id"] for value in result.json()["value"]}

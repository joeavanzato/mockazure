from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_security_roles import BASE, USER_ADMIN


async def test_role_metadata_context_paths(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(
            database, settings, tenant, app, None, {"RoleManagement.ReadWrite.Directory"}
        )
        response = await client.get(BASE + "/roleDefinitions", headers=headers)
        assert response.status_code == 200
        assert response.json()["@odata.context"].endswith(
            "#roleManagement/directory/roleDefinitions"
        )
        created = await client.post(
            BASE + "/roleAssignments",
            headers=headers,
            json={
                "principalId": str(user),
                "roleDefinitionId": USER_ADMIN,
                "directoryScopeId": "/",
            },
        )
        assert created.status_code == 201
        response = await client.get(BASE + "/roleAssignments?$expand=principal", headers=headers)
        assert response.json()["@odata.context"].endswith(
            "#roleManagement/directory/roleAssignments(principal())"
        )

from uuid import uuid4

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_security_roles import BASE, GLOBAL_READER, USER_ADMIN


async def test_bootstrap_roles_and_limited_legacy_disclosure(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        prefix = f"/_mock/tenants/{tenant}/identity/users/{user}/roles/"
        assert (await client.put(prefix + "User Administrator")).status_code == 200
        headers = await headers_for(
            database, settings, tenant, app, None, {"RoleManagement.Read.Directory"}
        )
        response = await client.get(
            BASE + "/roleAssignments",
            headers=headers,
            params={"$filter": f"principalId eq '{user}'"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["value"][0]["roleDefinitionId"] == USER_ADMIN
        member_path = f"/v1.0/directoryRoles(roleTemplateId='{USER_ADMIN}')/members"
        members = await client.get(member_path, headers=headers)
        assert members.status_code == 200, members.text
        assert members.json()["value"] == [
            {"id": str(user), "@odata.type": "#microsoft.graph.user"}
        ]
        alternate = await client.get(
            f"/v1.0/directoryRoles/roleTemplateId={USER_ADMIN}/members", headers=headers
        )
        assert alternate.status_code == 200 and alternate.json()["value"] == members.json()["value"]
        assert (await client.delete(prefix + "User Administrator")).status_code == 200
        role = await client.get(
            f"/v1.0/directoryRoles(roleTemplateId='{USER_ADMIN}')", headers=headers
        )
        assert role.status_code == 200
        assert (await client.get(member_path, headers=headers)).json()["value"] == []


async def test_role_snapshot_expansion_and_path_binding(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(
            database, settings, tenant, app, None, {"RoleManagement.ReadWrite.Directory"}
        )
        entries = []
        for definition in (USER_ADMIN, GLOBAL_READER):
            response = await client.post(
                BASE + "/roleAssignments",
                headers=headers,
                json={
                    "principalId": str(user),
                    "roleDefinitionId": definition,
                    "directoryScopeId": "/",
                },
            )
            assert response.status_code == 201
            entries.append(response.json())
        first = await client.get(
            BASE + "/roleAssignments",
            headers=headers,
            params={"$top": 1, "$expand": "roleDefinition($select=id,displayName)"},
        )
        assert first.status_code == 200, first.text
        page = first.json()
        assert set(page["value"][0]["roleDefinition"]) == {"id", "displayName"}
        remaining = next(x for x in entries if x["id"] != page["value"][0]["id"])
        assert (
            await client.delete(BASE + "/roleAssignments/" + remaining["id"], headers=headers)
        ).status_code == 204
        second = await client.get(page["@odata.nextLink"], headers=headers)
        assert second.status_code == 200, second.text
        assert second.json()["value"][0]["id"] == remaining["id"]
        assert second.json()["value"][0]["roleDefinition"]["id"] == remaining["roleDefinitionId"]
        changed = page["@odata.nextLink"].replace("roleAssignments?", "roleDefinitions?")
        assert (await client.get(changed, headers=headers)).status_code == 400
        assert (
            await client.get(page["@odata.nextLink"] + "&$top=2", headers=headers)
        ).status_code == 400


async def test_modern_role_reads_require_supported_delegated_role(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(
            database, settings, tenant, app, user, {"RoleManagement.Read.Directory"}, "delegated"
        )
        for collection in ("roleDefinitions", "roleAssignments"):
            assert (await client.get(BASE + "/" + collection, headers=headers)).status_code == 403
        # Legacy role enumeration permits an ordinary user with the API permission.
        assert (await client.get("/v1.0/directoryRoles", headers=headers)).status_code == 200
        assert (
            await client.put(f"/_mock/tenants/{tenant}/identity/users/{user}/roles/Global Reader")
        ).status_code == 200
        assert (await client.get(BASE + "/roleDefinitions", headers=headers)).status_code == 200
        write = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {"RoleManagement.ReadWrite.Directory"},
            "delegated",
        )
        response = await client.post(
            BASE + "/roleAssignments",
            headers=write,
            json={
                "principalId": str(user),
                "roleDefinitionId": USER_ADMIN,
                "directoryScopeId": "/",
            },
        )
        assert response.status_code == 403


async def test_unknown_role_assignments_and_unsupported_definition_fields(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(
            database, settings, tenant, app, None, {"RoleManagement.Read.Directory"}
        )
        unknown = await client.get(BASE + "/roleAssignments/" + str(uuid4()), headers=headers)
        assert unknown.status_code == 404
        invalid = await client.get(
            BASE + "/roleDefinitions?$select=rolePermissions", headers=headers
        )
        assert invalid.status_code == 400

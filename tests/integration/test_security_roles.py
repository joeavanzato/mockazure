from uuid import uuid4

from sqlalchemy import select

from mockazure.identity.models import ServicePrincipal
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_evidence_operations import drain_api
from tests.integration.test_graph_mutations import user_body

BASE = "/v1.0/roleManagement/directory"
USER_ADMIN = "fe930be7-5e62-47db-91af-98c3a49a38b1"
GLOBAL_READER = "f2ef992c-3afb-46b9-b7cf-a126ee74c451"
PRA = "e8611ab8-c189-46e8-94e1-60213ab1f814"


async def test_role_assignment_changes_effective_access(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        writer = await headers_for(
            database, settings, tenant, app, None, {"RoleManagement.ReadWrite.Directory"}
        )
        delegated = await headers_for(
            database, settings, tenant, app, user, {"User.ReadWrite.All"}, "delegated"
        )
        assert (
            await client.post("/v1.0/users", headers=delegated, json=user_body("role-denied"))
        ).status_code == 403
        added = await client.post(
            BASE + "/roleAssignments",
            headers=writer,
            json={
                "principalId": str(user),
                "roleDefinitionId": USER_ADMIN,
                "directoryScopeId": "/",
            },
        )
        assert added.status_code == 201, added.text
        assignment = added.json()
        result = await client.post("/v1.0/users", headers=delegated, json=user_body("role-allowed"))
        assert result.status_code == 201, result.text
        deleted = await client.delete(BASE + "/roleAssignments/" + assignment["id"], headers=writer)
        assert deleted.status_code == 204, deleted.text
        assert (
            await client.post("/v1.0/users", headers=delegated, json=user_body("role-denied-again"))
        ).status_code == 403


async def test_privileged_role_assignment_requires_privileged_admin(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        broad = await headers_for(
            database, settings, tenant, app, None, {"RoleManagement.ReadWrite.Directory"}
        )
        delegated = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {"RoleManagement.ReadWrite.Directory"},
            "delegated",
        )
        body = {"principalId": str(user), "roleDefinitionId": PRA, "directoryScopeId": "/"}
        assert (
            await client.post(BASE + "/roleAssignments", headers=delegated, json=body)
        ).status_code == 403
        added = await client.post(BASE + "/roleAssignments", headers=broad, json=body)
        assert added.status_code == 201, added.text
        for invalid in (
            dict(
                body,
                roleDefinitionId=USER_ADMIN,
                directoryScopeId=f"/administrativeUnits/{uuid4()}",
            ),
            dict(body, roleDefinitionId=USER_ADMIN, appScopeId="/"),
            dict(body, roleDefinitionId=str(uuid4())),
        ):
            response = await client.post(BASE + "/roleAssignments", headers=delegated, json=invalid)
            assert response.status_code in {400, 404}, response.text
        created = await client.post(
            BASE + "/roleAssignments",
            headers=delegated,
            json=dict(body, roleDefinitionId=USER_ADMIN),
        )
        assert created.status_code == 201, created.text
        duplicate = await client.post(
            BASE + "/roleAssignments",
            headers=delegated,
            json=dict(body, roleDefinitionId=USER_ADMIN),
        )
        assert duplicate.status_code in {400, 409}
        foreign = await client.post(
            "/_mock/tenants",
            json={"slug": "foreign-role", "display_name": "Foreign", "domain": "foreign.test"},
        )
        outsider = await client.post(
            f"/_mock/tenants/{foreign.json()['id']}/identity/users",
            json={
                "upn": "outsider@foreign.test",
                "display_name": "Outside",
                "password": "local-password-123",
            },
        )
        assert outsider.status_code == 201
        response = await client.post(
            BASE + "/roleAssignments",
            headers=broad,
            json=dict(body, principalId=outsider.json()["id"]),
        )
        assert response.status_code == 404, response.text
        assert (
            await client.delete(BASE + "/roleAssignments/" + added.json()["id"], headers=delegated)
        ).status_code == 204
        assert (
            await client.post(
                BASE + "/roleAssignments",
                headers=delegated,
                json=dict(body, roleDefinitionId=GLOBAL_READER),
            )
        ).status_code == 403


async def test_role_graph_and_legacy_views_match(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        base = f"/_mock/tenants/{tenant}/evidence"
        assert (
            await client.patch(base + "/settings", json={"delay_seconds": 0})
        ).status_code == 200
        headers = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {"RoleManagement.ReadWrite.Directory", "User.Read.All"},
        )
        definitions = await client.get(
            BASE + "/roleDefinitions",
            headers=headers,
            params={"$filter": "displayName eq 'User Administrator'"},
        )
        assert definitions.status_code == 200, definitions.text
        definition = definitions.json()["value"][0]
        assert definition["id"] == definition["templateId"] == USER_ADMIN
        assert definition["isBuiltIn"] is True
        response = await client.post(
            BASE + "/roleAssignments",
            headers=headers,
            json={
                "principalId": str(user),
                "roleDefinitionId": definition["id"],
                "directoryScopeId": "/",
            },
        )
        assert response.status_code == 201, response.text
        assignment = response.json()
        modern = await client.get(
            BASE + "/roleAssignments",
            headers=headers,
            params={"$filter": f"principalId eq '{user}'", "$expand": "principal"},
        )
        assert modern.status_code == 200, modern.text
        assert modern.json()["value"][0]["principal"]["id"] == str(user)
        legacy = await client.get(
            "/v1.0/directoryRoles",
            headers=headers,
            params={"$filter": f"roleTemplateId eq '{USER_ADMIN}'"},
        )
        assert legacy.status_code == 200, legacy.text
        role = legacy.json()["value"][0]
        assert role["id"] != USER_ADMIN and role["roleTemplateId"] == USER_ADMIN
        members = await client.get(
            "/v1.0/directoryRoles/" + role["id"] + "/members", headers=headers
        )
        assert members.status_code == 200, members.text
        assert members.json()["value"][0]["id"] == str(user)
        assert members.json()["value"][0]["displayName"] == "Directory Reader"
        assert (
            await client.delete(BASE + "/roleAssignments/" + assignment["id"], headers=headers)
        ).status_code == 204
        retained = await client.get("/v1.0/directoryRoles/" + role["id"], headers=headers)
        assert retained.status_code == 200, retained.text
        members = await client.get(
            "/v1.0/directoryRoles/" + role["id"] + "/members", headers=headers
        )
        assert members.json()["value"] == []
        await drain_api(client, tenant)
        exported = await client.get(base + "/export?family=directoryAudit")
        assert exported.status_code == 200, exported.text
        names = {x["payload"]["activityDisplayName"] for x in exported.json()["records"]}
        assert {"Add directory role assignment", "Remove directory role assignment"} <= names


async def test_service_principal_role_controls_exchange_operation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(
            database, settings, tenant, app, None, {"RoleManagement.ReadWrite.Directory"}
        )
        exchange = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {"Exchange.ManageAsApp"},
            audience="https://outlook.office365.com",
        )
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        path = f"/adminapi/v2.0/{tenant}/InvokeCommand"
        command = {"CmdletInput": {"CmdletName": "Get-OrganizationConfig", "Parameters": {}}}
        assert (await client.post(path, headers=exchange, json=command)).status_code == 403
        assigned = await client.post(
            BASE + "/roleAssignments",
            headers=headers,
            json={
                "principalId": str(sp),
                "roleDefinitionId": GLOBAL_READER,
                "directoryScopeId": "/",
            },
        )
        assert assigned.status_code == 201, assigned.text
        assert (await client.post(path, headers=exchange, json=command)).status_code == 200
        assert (
            await client.delete(BASE + "/roleAssignments/" + assigned.json()["id"], headers=headers)
        ).status_code == 204
        assert (await client.post(path, headers=exchange, json=command)).status_code == 403

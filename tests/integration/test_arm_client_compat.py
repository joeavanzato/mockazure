from sqlalchemy import select

from mockazure.identity.models import ServicePrincipal
from tests.integration.graph_support import graph_client, registered_app
from tests.integration.test_arm_authorization import assign
from tests.integration.test_arm_inventory import access
from tests.integration.test_arm_state import seed_arm


async def test_group_head_matches_pinned_azure_cmdlet_contract(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(client, base, sp, f"/subscriptions/{sub}", "Reader")
        args = {"api-version": "2021-04-01"}
        found = await client.head(group, params=args, headers=headers)
        assert found.status_code == 204 and not found.content
        assert (
            await client.head(group + "-absent", params=args, headers=headers)
        ).status_code == 404
        denied = await registered_app(client, tenant)
        denied_headers = await access(database, settings, tenant, denied)
        for path in (group, group + "-absent"):
            assert (await client.head(path, params=args, headers=denied_headers)).status_code == 403
        assert (
            await client.head(group, params={"api-version": "unknown"}, headers=headers)
        ).status_code == 400


async def test_original_az_group_patch_preserves_identity(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(client, base, sp, f"/subscriptions/{sub}", "Contributor")
        args = {"api-version": "2021-04-01"}
        name = group.rsplit("/", 1)[1]
        payload = {"name": name, "properties": {}, "tags": {"Updated": "application"}}
        changed = await client.patch(group, params=args, headers=headers, json=payload)
        assert changed.status_code == 200, changed.text
        assert changed.json()["tags"] == payload["tags"]
        for invalid in (
            {**payload, "name": name + "-other"},
            {**payload, "properties": {"provisioningState": "Failed"}},
        ):
            assert (
                await client.patch(group, params=args, headers=headers, json=invalid)
            ).status_code == 400
        assert (await client.get(group, params=args, headers=headers)).json()["name"] == name


async def test_original_az_tag_uri_and_2024_version(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(client, base, sp, f"/subscriptions/{sub}", "Contributor")
        # Azure's generated tag client joins its scope parameter with an extra slash.
        path = "/" + group + "/providers/Microsoft.Resources/tags/default"
        request = client.build_request(
            "PUT",
            group,
            headers=headers,
            params={"api-version": "2024-07-01"},
            json={"properties": {"tags": {"Evidence": "az"}}},
        )
        request.url = request.url.copy_with(raw_path=(path + "?api-version=2024-07-01").encode())
        response = await client.send(request)
        assert response.status_code == 200, response.text
        assert response.json()["properties"]["tags"] == {"Evidence": "az"}
        assert (
            await client.get(group, params={"api-version": "2024-07-01"}, headers=headers)
        ).status_code == 400


async def test_original_az_role_definition_preview(database, settings):
    from mockazure.providers.arm.contracts import validate_arm_payload

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(client, base, sp, f"/subscriptions/{sub}", "Reader")
        path = f"//subscriptions/{sub}/providers/Microsoft.Authorization/roleDefinitions"
        request = client.build_request("GET", group, headers=headers)
        request.url = request.url.copy_with(
            raw_path=(path + "?api-version=2022-05-01-preview").encode()
        )
        response = await client.send(request)
        assert response.status_code == 200, response.text
        assert len(response.json()["value"]) == 20
        for item in response.json()["value"]:
            validate_arm_payload("RoleDefinition", item, namespace="definitionsPreview")


async def test_original_az_assignment_preview(database, settings):
    from uuid import uuid4

    from mockazure.providers.arm.contracts import validate_arm_payload

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(client, base, sp, f"/subscriptions/{sub}", "Owner")
        role = (
            f"{group}/providers/Microsoft.Authorization/roleDefinitions/"
            "acdd72a7-3385-48ef-bd42-f606fba81ae7"
        )
        path = group + "/providers/Microsoft.Authorization/roleAssignments/" + str(uuid4())
        response = await client.put(
            path,
            params={"api-version": "2020-08-01-preview"},
            headers=headers,
            json={
                "properties": {
                    "principalId": str(user),
                    "principalType": "User",
                    "roleDefinitionId": role,
                }
            },
        )
        assert response.status_code == 201, response.text
        validate_arm_payload("RoleAssignment", response.json(), namespace="assignmentsPreview")
        for invalid in (
            role.replace(str(sub), str(uuid4())),
            role.replace(group, group + "-unrelated"),
        ):
            denied = await client.put(
                path.rsplit("/", 1)[0] + "/" + str(uuid4()),
                params={"api-version": "2020-08-01-preview"},
                headers=headers,
                json={
                    "properties": {
                        "principalId": str(user),
                        "principalType": "User",
                        "roleDefinitionId": invalid,
                    }
                },
            )
            assert denied.status_code == 400

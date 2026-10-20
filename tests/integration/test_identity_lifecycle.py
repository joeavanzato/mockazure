from tests.integration.graph_support import graph_client, headers_for


async def create_user(client, tenant, name, domain="contoso.test"):
    response = await client.post(
        f"/_mock/tenants/{tenant}/identity/users",
        json={
            "upn": name + "@" + domain,
            "display_name": name,
            "password": "Local manager password!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_manager_assignment_cycle_foreign_and_removal_are_atomic(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        manager = await create_user(client, tenant, "manager")
        headers = await headers_for(database, settings, tenant, app, None, {"User.ReadWrite.All"})
        reader = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {"User.Read.All"},
            kind="delegated",
        )
        base = f"/v1.0/users/{user}/manager"
        response = await client.put(
            base + "/$ref",
            headers=headers,
            json={"@odata.id": settings.endpoints.graph + "/v1.0/users/" + manager},
        )
        assert response.status_code == 204, response.text
        assert (await client.get(base, headers=headers)).status_code == 403
        read = await client.get(base, headers=reader)
        assert read.status_code == 200 and read.json()["id"] == manager
        cycle = await client.put(
            f"/v1.0/users/{manager}/manager/$ref",
            headers=headers,
            json={"@odata.id": settings.endpoints.graph + f"/v1.0/users/{user}"},
        )
        assert cycle.status_code == 400
        other = await client.post(
            "/_mock/tenants",
            json={"slug": "foreign-manager", "display_name": "Foreign", "domain": "foreign.test"},
        )
        foreign_id = await create_user(client, other.json()["id"], "foreign", "foreign.test")
        foreign = await client.put(
            base + "/$ref",
            headers=headers,
            json={"@odata.id": settings.endpoints.graph + f"/v1.0/users/{foreign_id}"},
        )
        assert foreign.status_code == 404
        assert (await client.get(base, headers=reader)).json()["id"] == manager
        assert (await client.delete(base + "/$ref", headers=headers)).status_code == 204
        assert (await client.get(base, headers=reader)).status_code == 404
        assert (await client.get("/v1.0/users/" + manager, headers=headers)).status_code == 200


async def test_manager_mutation_rejects_missing_permission_and_external_reference(
    database, settings
):
    async with graph_client(database, settings) as (client, tenant, app, user):
        manager = await create_user(client, tenant, "manager")
        path = f"/v1.0/users/{user}/manager/$ref"
        read = await headers_for(database, settings, tenant, app, None, {"User.Read.All"})
        body = {"@odata.id": settings.endpoints.graph + "/v1.0/users/" + manager}
        assert (await client.put(path, headers=read, json=body)).status_code == 403
        write = await headers_for(database, settings, tenant, app, None, {"User.ReadWrite.All"})
        assert (
            await client.put(
                path,
                headers=write,
                json={"@odata.id": "https://untrusted.example/users/" + manager},
            )
        ).status_code == 400


async def assign_application_access(client, tenant, headers):
    from mockazure.security.catalog import permission_id
    from tests.integration.test_security_grants import sp_ids
    from tests.integration.test_security_mutations import create_operational_app

    app = await create_operational_app(client, tenant, headers)
    resource, sp = await sp_ids(client, headers, app["appId"])
    password = await client.post(
        f"/v1.0/applications/{app['id']}/addPassword", headers=headers, json={}
    )
    assert password.status_code == 200, password.text
    path = f"/v1.0/servicePrincipals/{sp}/appRoleAssignments"
    assignment = await client.post(
        path,
        headers=headers,
        json={
            "principalId": sp,
            "resourceId": resource,
            "appRoleId": str(permission_id("User.Read.All", "application")),
        },
    )
    assert assignment.status_code == 201, assignment.text
    return app, password.json()["secretText"], path + "/" + assignment.json()["id"]


async def test_joiner_can_use_only_assigned_services(database, settings):
    from tests.integration.test_security_grants import WRITE
    from tests.integration.test_security_mutations import token

    async with graph_client(database, settings) as (client, tenant, operator, _):
        headers = await headers_for(database, settings, tenant, operator, None, WRITE)
        app, secret, _ = await assign_application_access(client, tenant, headers)
        response = await token(client, tenant, app, secret)
        assert response.status_code == 200, response.text
        actor = {"Authorization": "Bearer " + response.json()["access_token"]}
        assert (await client.get("/v1.0/users", headers=actor)).status_code == 200
        assert (await client.get("/v1.0/organization", headers=actor)).status_code == 200
        assert (await client.get("/v1.0/groups", headers=actor)).status_code == 403


async def test_mover_loses_removed_access(database, settings):
    from tests.integration.test_security_grants import WRITE
    from tests.integration.test_security_mutations import token

    async with graph_client(database, settings) as (client, tenant, operator, _):
        headers = await headers_for(database, settings, tenant, operator, None, WRITE)
        app, secret, path = await assign_application_access(client, tenant, headers)
        before = await token(client, tenant, app, secret)
        actor = {"Authorization": "Bearer " + before.json()["access_token"]}
        assert (await client.get("/v1.0/users", headers=actor)).status_code == 200
        assert (await client.delete(path, headers=headers)).status_code == 204
        after = await token(client, tenant, app, secret)
        assert after.status_code == 200, after.text
        changed = {"Authorization": "Bearer " + after.json()["access_token"]}
        assert (await client.get("/v1.0/users", headers=changed)).status_code == 403
        assert (await client.get("/v1.0/organization", headers=changed)).status_code == 200
        # Grant removal changes issuance; it is not an access-token revocation promise.
        assert (await client.get("/v1.0/users", headers=actor)).status_code == 200

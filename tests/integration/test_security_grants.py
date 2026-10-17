from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import jwt
from sqlalchemy import select

from mockazure.identity.models import DirectoryRole, Grant, ServicePrincipal
from mockazure.security.catalog import permission_id
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.identity_support import identity_client
from tests.integration.test_identity_authorization import CALLBACK, VERIFIER, provision, sign_in
from tests.integration.test_security_mutations import create_operational_app, token

WRITE = {
    "AppRoleAssignment.ReadWrite.All",
    "Application.Read.All",
    "Application.ReadWrite.All",
    "DelegatedPermissionGrant.ReadWrite.All",
    "Directory.Read.All",
}


async def sp_ids(client, headers, app_id):
    graph = await client.get(
        "/v1.0/servicePrincipals(appId='00000003-0000-0000-c000-000000000000')", headers=headers
    )
    target = await client.get(f"/v1.0/servicePrincipals(appId='{app_id}')", headers=headers)
    assert graph.status_code == target.status_code == 200
    return graph.json()["id"], target.json()["id"]


async def test_graph_grant_token_effect(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        headers = await headers_for(database, settings, tenant, actor, None, WRITE)
        app = await create_operational_app(client, tenant, headers)
        resource, sp = await sp_ids(client, headers, app["appId"])
        password = (
            await client.post(
                f"/v1.0/applications/{app['id']}/addPassword", headers=headers, json={}
            )
        ).json()["secretText"]
        before = await token(client, tenant, app, password)
        before_headers = {"Authorization": "Bearer " + before.json()["access_token"]}
        assert (await client.get("/v1.0/users", headers=before_headers)).status_code == 403
        path = f"/v1.0/servicePrincipals/{sp}/appRoleAssignments"
        body = {
            "principalId": sp,
            "resourceId": resource,
            "appRoleId": str(permission_id("User.Read.All", "application")),
        }
        result = await client.post(path, headers=headers, json=body)
        assert result.status_code == 201, result.text
        assignment = result.json()
        access = await token(client, tenant, app, password)
        claims = jwt.decode(access.json()["access_token"], options={"verify_signature": False})
        assert {"Organization.Read.All", "User.Read.All"} == set(claims["roles"])
        access_headers = {"Authorization": "Bearer " + access.json()["access_token"]}
        assert (await client.get("/v1.0/users", headers=access_headers)).status_code == 200
        duplicate = await client.post(path, headers=headers, json=body)
        assert duplicate.status_code in {400, 409}, duplicate.text
        by_resource = await client.get(
            f"/v1.0/servicePrincipals/{resource}/appRoleAssignedTo", headers=headers
        )
        assert by_resource.status_code == 200, by_resource.text
        assert assignment["id"] in {x["id"] for x in by_resource.json()["value"]}
        deleted = await client.delete(
            f"/v1.0/servicePrincipals/{resource}/appRoleAssignedTo/{assignment['id']}",
            headers=headers,
        )
        assert deleted.status_code == 204, deleted.text
        new = await token(client, tenant, app, password)
        assert (
            "User.Read.All"
            not in jwt.decode(new.json()["access_token"], options={"verify_signature": False})[
                "roles"
            ]
        )
        assert (await client.get("/v1.0/users", headers=access_headers)).status_code == 200


async def test_delegated_grant_refresh_revocation(database, settings):
    async with identity_client(database, settings) as (client, tenant, actor, *_):
        user, app = await provision(client, tenant, ["User.Read", "Organization.Read.All"])
        headers = await headers_for(database, settings, tenant, actor, None, WRITE)
        resource, sp = await sp_ids(client, headers, app["client_id"])
        response = await client.post(
            "/v1.0/oauth2PermissionGrants",
            headers=headers,
            json={
                "clientId": sp,
                "resourceId": resource,
                "consentType": "AllPrincipals",
                "scope": "User.Read Organization.Read.All",
            },
        )
        assert response.status_code == 201, response.text
        grant = response.json()
        assert grant["clientId"] == sp and grant["principalId"] is None
        signed = await sign_in(
            client, tenant, app, scope="openid offline_access User.Read Organization.Read.All"
        )
        assert signed.status_code == 303, signed.text
        code = parse_qs(urlsplit(signed.headers["location"]).query)["code"][0]
        response = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "authorization_code",
                "client_id": app["client_id"],
                "redirect_uri": CALLBACK,
                "code": code,
                "code_verifier": VERIFIER,
            },
        )
        assert response.status_code == 200, response.text
        refresh = response.json()["refresh_token"]
        assert set(
            jwt.decode(response.json()["access_token"], options={"verify_signature": False})[
                "scp"
            ].split()
        ) == {"User.Read", "Organization.Read.All"}
        patched = await client.patch(
            "/v1.0/oauth2PermissionGrants/" + grant["id"],
            headers=headers,
            json={"scope": "User.Read"},
        )
        assert patched.status_code == 204, patched.text
        refreshed = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "refresh_token",
                "client_id": app["client_id"],
                "refresh_token": refresh,
            },
        )
        assert refreshed.status_code == 400 and refreshed.json()["error"] == "invalid_grant"
        deleted = await client.delete(
            "/v1.0/oauth2PermissionGrants/" + grant["id"], headers=headers
        )
        assert deleted.status_code == 204


async def test_existing_admin_grants_visible_and_isolated(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        headers = await headers_for(database, settings, tenant, actor, None, WRITE)
        resource, sp = await sp_ids(client, headers, actor)
        path = f"/v1.0/servicePrincipals(appId='{actor}')/appRoleAssignments"
        response = await client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        expected = str(permission_id("Organization.Read.All", "application"))
        found = next(x for x in response.json()["value"] if x["appRoleId"] == expected)
        assert found["principalId"] == sp and found["resourceId"] == resource
        assert (await client.get(path, headers=headers)).json()["value"] == response.json()["value"]
        other = await client.post(
            "/_mock/tenants",
            json={"slug": "outside", "display_name": "Outside", "domain": "outside.test"},
        )
        foreign = await client.post(
            f"/_mock/tenants/{other.json()['id']}/identity/applications",
            json={"display_name": "Foreign", "client_mode": "public"},
        )
        async with database.sessions() as session:
            foreign_sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.client_id == UUID(foreign.json()["client_id"])
                )
            )
        for data in (
            {"principalId": str(foreign_sp), "resourceId": resource, "appRoleId": expected},
            {"principalId": sp, "resourceId": str(foreign_sp), "appRoleId": expected},
            {"principalId": sp, "resourceId": resource, "appRoleId": str(uuid4())},
        ):
            invalid = await client.post(path, headers=headers, json=data)
            assert invalid.status_code in {400, 404}, invalid.text
        async with database.sessions() as session:
            grants = list(await session.scalars(select(Grant).where(Grant.tenant_id == tenant)))
            assert len(grants) == 1 and grants[0].permissions == ["Organization.Read.All"]


async def test_grant_privilege_checks(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        headers = await headers_for(database, settings, tenant, actor, None, WRITE)
        resource, sp = await sp_ids(client, headers, actor)
        path = f"/v1.0/servicePrincipals/{sp}/appRoleAssignments"
        body = {
            "principalId": sp,
            "resourceId": resource,
            "appRoleId": str(permission_id("User.Read.All", "application")),
        }
        for permissions in ({"Application.Read.All"}, {"AppRoleAssignment.ReadWrite.All"}):
            limited = await headers_for(database, settings, tenant, actor, None, permissions)
            assert (await client.post(path, headers=limited, json=body)).status_code == 403
        delegated = await headers_for(database, settings, tenant, actor, user, WRITE, "delegated")
        async with database.sessions.begin() as session:
            session.add(
                DirectoryRole(tenant_id=tenant, user_id=user, role="Application Administrator")
            )
        assert (await client.post(path, headers=delegated, json=body)).status_code == 403
        async with database.sessions.begin() as session:
            session.add(
                DirectoryRole(tenant_id=tenant, user_id=user, role="Privileged Role Administrator")
            )
        result = await client.post(path, headers=delegated, json=body)
        assert result.status_code == 201, result.text

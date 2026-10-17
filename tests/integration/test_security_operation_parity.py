"""Operational Graph contracts use canonical state and actual local HTTP."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from mockazure.identity.models import DirectoryRole, ServicePrincipal
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_security_mutations import create_operational_app, token


async def test_alternate_application_and_sp_mutations(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        headers = await headers_for(
            database,
            settings,
            tenant,
            actor,
            None,
            {"Application.ReadWrite.All", "Directory.Read.All"},
        )
        app = await create_operational_app(client, tenant, headers)
        for resource in ("applications", "servicePrincipals"):
            path = f"/v1.0/{resource}(appId='{app['appId']}')"
            changed = await client.patch(path, headers=headers, json={"displayName": resource})
            assert changed.status_code == 204, changed.text
            assert (await client.get(path, headers=headers)).json()["displayName"] == resource
            added = await client.post(path + "/addPassword", headers=headers, json={})
            assert added.status_code == 200, added.text
            assert (await token(client, tenant, app, added.json()["secretText"])).status_code == 200
            removed = await client.post(
                path + "/removePassword", headers=headers, json={"keyId": added.json()["keyId"]}
            )
            assert removed.status_code == 204, removed.text
            assert (await token(client, tenant, app, added.json()["secretText"])).status_code == 401
            ref = {"@odata.id": f"https://graph.microsoft.com/v1.0/users/{user}"}
            assert (
                await client.post(path + "/owners/$ref", headers=headers, json=ref)
            ).status_code == 204
            owners = await client.get(path + "/owners", headers=headers)
            assert owners.status_code == 200, owners.text
            assert str(user) in {x["id"] for x in owners.json()["value"]}
            assert (
                await client.delete(path + f"/owners/{user}/$ref", headers=headers)
            ).status_code == 204
        for resource in ("servicePrincipals", "applications"):
            path = f"/v1.0/{resource}(appId='{app['appId']}')"
            assert (await client.delete(path, headers=headers)).status_code == 204
            assert (await client.get(path, headers=headers)).status_code == 404


async def test_deleted_app_cannot_be_reenabled_from_admin(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        headers = await headers_for(
            database, settings, tenant, actor, None, {"Application.ReadWrite.All"}
        )
        app = await create_operational_app(client, tenant, headers)
        path = "/v1.0/applications/" + app["id"]
        secret = (await client.post(path + "/addPassword", headers=headers, json={})).json()[
            "secretText"
        ]
        assert (await client.delete(path, headers=headers)).status_code == 204
        result = await client.patch(
            f"/_mock/tenants/{tenant}/identity/applications/{app['appId']}", json={"enabled": True}
        )
        assert result.status_code == 404, result.text
        assert (await token(client, tenant, app, secret)).status_code == 401


async def test_device_alternate_key_uniqueness_and_owner_type(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        async with database.sessions.begin() as session:
            session.add(DirectoryRole(tenant_id=tenant, user_id=user, role="Global Administrator"))
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == actor
                )
            )
        headers = await headers_for(
            database,
            settings,
            tenant,
            actor,
            user,
            {"Directory.AccessAsUser.All", "Device.Read.All"},
            "delegated",
        )
        body = {"displayName": "Device", "deviceId": str(uuid4()), "operatingSystem": "Linux"}
        first = await client.post("/v1.0/devices", headers=headers, json=body)
        assert first.status_code == 201, first.text
        duplicate = await client.post("/v1.0/devices", headers=headers, json=body)
        assert duplicate.status_code == 409, duplicate.text
        path = f"/v1.0/devices(deviceId='{body['deviceId']}')"
        result = await client.get(path, headers=headers)
        assert result.status_code == 200 and result.json()["id"] == first.json()["id"], result.text
        assert (
            await client.patch(path, headers=headers, json={"displayName": "Renamed"})
        ).status_code == 204
        bad_owner = await client.post(
            path + "/registeredOwners/$ref",
            headers=headers,
            json={"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{sp}"},
        )
        assert bad_owner.status_code == 400, bad_owner.text
        assert (
            await client.post(
                path + "/registeredOwners/$ref",
                headers=headers,
                json={"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user}"},
            )
        ).status_code == 204
        assert (await client.get(path + "/registeredOwners", headers=headers)).status_code == 200
        assert (
            await client.delete(path + f"/registeredOwners/{user}/$ref", headers=headers)
        ).status_code == 204
        assert (await client.delete(path, headers=headers)).status_code == 204


@pytest.mark.parametrize("role", ["Cloud Device Administrator", "Windows 365 Administrator"])
async def test_device_update_role_property_constraints(database, settings, role):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        async with database.sessions.begin() as session:
            row = DirectoryRole(tenant_id=tenant, user_id=user, role="Global Administrator")
            session.add(row)
        headers = await headers_for(
            database,
            settings,
            tenant,
            actor,
            user,
            {"Directory.AccessAsUser.All", "Device.Read.All"},
            "delegated",
        )
        device = await client.post(
            "/v1.0/devices",
            headers=headers,
            json={"displayName": "Role-bound device", "operatingSystem": "Windows"},
        )
        assert device.status_code == 201
        path = "/v1.0/devices/" + device.json()["id"]
        async with database.sessions.begin() as session:
            await session.delete(
                await session.get(DirectoryRole, (tenant, user, "Global Administrator"))
            )
            session.add(DirectoryRole(tenant_id=tenant, user_id=user, role=role))
        allowed = (
            {"accountEnabled": False}
            if role == "Cloud Device Administrator"
            else {"displayName": "Basic"}
        )
        denied = (
            {"displayName": "Forbidden"}
            if role == "Cloud Device Administrator"
            else {"accountEnabled": False}
        )
        assert (await client.patch(path, headers=headers, json=allowed)).status_code == 204
        response = await client.patch(path, headers=headers, json=denied)
        assert response.status_code == 403, response.text
        if role == "Cloud Device Administrator":
            response = await client.post(
                "/v1.0/devices", headers=headers, json={"displayName": "Forbidden"}
            )
            assert response.status_code == 403, response.text


async def test_unenforced_sp_policy_rejected(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        headers = await headers_for(
            database, settings, tenant, actor, None, {"Application.ReadWrite.All"}
        )
        app = await create_operational_app(client, tenant, headers)
        read = await client.get(f"/v1.0/servicePrincipals(appId='{app['appId']}')", headers=headers)
        path = "/v1.0/servicePrincipals/" + read.json()["id"]
        result = await client.patch(path, headers=headers, json={"appRoleAssignmentRequired": True})
        assert result.status_code == 400, result.text

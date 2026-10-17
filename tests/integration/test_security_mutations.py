import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import serialization
from sqlalchemy import func, select

from mockazure.evidence.models import EvidenceSource
from mockazure.identity.models import ServicePrincipal
from tests.integration.graph_support import graph_client, headers_for, registered_app
from tests.integration.identity_support import certificate
from tests.integration.test_identity_app_only import assertion


async def create_operational_app(client, tenant, headers):
    result = await client.post(
        "/v1.0/applications",
        headers=headers,
        json={
            "displayName": "Security test client",
            "requiredResourceAccess": [
                {
                    "resourceAppId": "00000003-0000-0000-c000-000000000000",
                    "resourceAccess": [
                        {"id": "498476ce-e0fe-48b0-b801-37ba7e2685c6", "type": "Role"}
                    ],
                }
            ],
        },
    )
    assert result.status_code == 201, result.text
    app = result.json()
    result = await client.post(
        "/v1.0/servicePrincipals", headers=headers, json={"appId": app["appId"]}
    )
    assert result.status_code == 201, result.text
    grant = await client.put(
        f"/_mock/tenants/{tenant}/identity/grants/{uuid4()}",
        json={
            "client_id": app["appId"],
            "resource": "graph",
            "kind": "application",
            "permissions": ["Organization.Read.All"],
        },
    )
    assert grant.status_code == 200, grant.text
    return app


async def token(client, tenant, app, secret):
    return await client.post(
        f"/{tenant}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": app["appId"],
            "client_secret": secret,
            "scope": "https://graph.microsoft.com/.default",
        },
    )


async def test_application_password_oauth_roundtrip(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor_app, user):
        headers = await headers_for(
            database, settings, tenant, actor_app, None, {"Application.ReadWrite.All"}
        )
        app = await create_operational_app(client, tenant, headers)
        path = "/v1.0/applications/" + app["id"]
        result = await client.post(
            path + "/addPassword",
            headers=headers,
            json={"passwordCredential": {"displayName": "Test"}},
        )
        assert result.status_code == 200, result.text
        password = result.json()
        access = await token(client, tenant, app, password["secretText"])
        assert access.status_code == 200, access.text
        current = await client.get(path, headers=headers)
        assert any(x["keyId"] == password["keyId"] for x in current.json()["passwordCredentials"])
        assert password["secretText"] not in current.text
        result = await client.post(
            path + "/removePassword", headers=headers, json={"keyId": password["keyId"]}
        )
        assert result.status_code == 204, result.text
        assert (await token(client, tenant, app, password["secretText"])).status_code == 401
        issued = await client.get(
            "/v1.0/organization",
            headers={"Authorization": "Bearer " + access.json()["access_token"]},
        )
        assert issued.status_code == 200
        future = await client.post(
            path + "/addPassword",
            headers=headers,
            json={
                "passwordCredential": {
                    "startDateTime": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "endDateTime": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
                }
            },
        )
        assert future.status_code == 200, future.text
        assert (await token(client, tenant, app, future.json()["secretText"])).status_code == 401
        assert (await client.delete(path, headers=headers)).status_code == 204
        assert (await client.get(path, headers=headers)).status_code == 404
        assert (await token(client, tenant, app, future.json()["secretText"])).status_code == 401


async def test_certificate_credentials(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor_app, user):
        headers = await headers_for(
            database, settings, tenant, actor_app, None, {"Application.ReadWrite.All"}
        )
        app = await create_operational_app(client, tenant, headers)
        key, cert = certificate()
        path = "/v1.0/applications/" + app["id"]
        key_id = str(uuid4())
        response = await client.patch(
            path,
            headers=headers,
            json={
                "keyCredentials": [
                    {
                        "keyId": key_id,
                        "type": "AsymmetricX509Cert",
                        "usage": "Verify",
                        "displayName": "Public lab certificate",
                        "key": base64.b64encode(
                            cert.public_bytes(serialization.Encoding.DER)
                        ).decode(),
                    }
                ]
            },
        )
        assert response.status_code == 204, response.text
        fields = {
            "grant_type": "client_credentials",
            "client_id": app["appId"],
            "scope": "https://graph.microsoft.com/.default",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": assertion(key, cert, tenant, app["appId"]),
        }
        response = await client.post(f"/{tenant}/oauth2/v2.0/token", data=fields)
        assert response.status_code == 200, response.text
        current = await client.get(path, headers=headers)
        assert current.status_code == 200 and "private" not in current.text.lower()
        assert "verifier" not in current.text
        assert any(x["keyId"] == key_id for x in current.json()["keyCredentials"])
        assert (
            await client.patch(path, headers=headers, json={"keyCredentials": []})
        ).status_code == 204
        fields["client_assertion"] = assertion(key, cert, tenant, app["appId"])
        assert (await client.post(f"/{tenant}/oauth2/v2.0/token", data=fields)).status_code == 401


async def test_owner_scoped_write_and_cross_tenant(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor_app, user):
        own = await headers_for(
            database, settings, tenant, actor_app, None, {"Application.ReadWrite.OwnedBy"}
        )
        response = await client.post(
            "/v1.0/applications", headers=own, json={"displayName": "Owned application"}
        )
        assert response.status_code == 201, response.text
        path = "/v1.0/applications/" + response.json()["id"]
        assert (
            await client.patch(path, headers=own, json={"displayName": "Owned update"})
        ).status_code == 204
        other = await registered_app(client, tenant)
        foreign = await headers_for(
            database, settings, tenant, other, None, {"Application.ReadWrite.OwnedBy"}
        )
        assert (
            await client.patch(path, headers=foreign, json={"displayName": "Forbidden"})
        ).status_code == 403
        broad = await headers_for(
            database,
            settings,
            tenant,
            actor_app,
            None,
            {"Application.ReadWrite.All", "Directory.Read.All"},
        )
        ref = {"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user}"}
        assert (await client.post(path + "/owners/$ref", headers=own, json=ref)).status_code == 403
        result = await client.post(path + "/owners/$ref", headers=broad, json=ref)
        assert result.status_code == 204, result.text
        owners = await client.get(path + "/owners", headers=broad)
        assert owners.status_code == 200 and str(user) in {x["id"] for x in owners.json()["value"]}
        bad = await client.post(
            path + "/owners/$ref",
            headers=broad,
            json={"@odata.id": f"https://evil.test/directoryObjects/{user}"},
        )
        assert bad.status_code == 400
        missing = await client.post(
            path + "/owners/$ref",
            headers=broad,
            json={"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{uuid4()}"},
        )
        assert missing.status_code == 404
        async with database.sessions() as session:
            actor_sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == actor_app
                )
            )
        result = await client.delete(path + f"/owners/{actor_sp}/$ref", headers=broad)
        assert result.status_code == 204, result.text
        assert (
            await client.patch(path, headers=own, json={"displayName": "Lost ownership"})
        ).status_code == 403


async def test_security_mutation_audit_atomic(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor_app, user):
        headers = await headers_for(
            database, settings, tenant, actor_app, None, {"Application.ReadWrite.All"}
        )
        app = await create_operational_app(client, tenant, headers)
        path = "/v1.0/applications/" + app["id"]
        password = await client.post(path + "/addPassword", headers=headers, json={})
        assert password.status_code == 200, password.text
        secret = password.json()["secretText"]
        async with database.sessions() as session:
            before = await session.scalar(
                select(func.count())
                .select_from(EvidenceSource)
                .where(EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "security")
            )
            sources = list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "security"
                    )
                )
            )
            original = json.dumps([s.facts for s in sources], sort_keys=True)
        assert before >= 3
        assert secret not in original and "verifier" not in original
        result = await client.patch(
            path, headers=headers, json={"displayName": "Must roll back", "invented": True}
        )
        assert result.status_code == 400, result.text
        current = await client.get(path, headers=headers)
        assert current.json()["displayName"] == "Security test client"
        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceSource)
                    .where(EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "security")
                )
                == before
            )
            sources_after = list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "security"
                    )
                )
            )
            assert json.dumps([s.facts for s in sources_after], sort_keys=True) == original
        from mockazure.evidence.projectors import project_source
        from mockazure.evidence.repository import load_source
        from mockazure.evidence.schemas import SourceRef

        async with database.sessions() as session:
            source = await load_source(
                session, SourceRef(tenant_id=tenant, kind="security", id=sources[0].id)
            )
            projected = project_source(source)
        from mockazure.evidence.datasets import validate_public_payload

        validate_public_payload("v1.0", "directoryAudit", projected.items[0].payload)
        assert projected.items[0].family == "directoryAudit"
        assert projected.items[0].payload["result"] == "success"
        assert UUID(projected.items[0].payload["targetResources"][0]["id"])


async def test_device_delegated_creation_and_enforcement(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor_app, user):
        app_headers = await headers_for(
            database, settings, tenant, actor_app, None, {"Device.ReadWrite.All"}
        )
        body = {
            "displayName": "Windows device",
            "operatingSystem": "Windows",
            "accountEnabled": True,
        }
        assert (
            await client.post("/v1.0/devices", headers=app_headers, json=body)
        ).status_code == 403
        delegated = await headers_for(
            database,
            settings,
            tenant,
            actor_app,
            user,
            {"Directory.AccessAsUser.All", "Device.Read.All"},
            "delegated",
        )
        assert (await client.post("/v1.0/devices", headers=delegated, json=body)).status_code == 403
        role = await client.put(
            f"/_mock/tenants/{tenant}/identity/users/{user}/roles/Global Administrator"
        )
        assert role.status_code == 200
        created = await client.post("/v1.0/devices", headers=delegated, json=body)
        assert created.status_code == 201, created.text
        path = "/v1.0/devices/" + created.json()["id"]
        assert (
            await client.patch(path, headers=delegated, json={"accountEnabled": False})
        ).status_code == 204
        current = await client.get(path, headers=delegated)
        assert current.json()["accountEnabled"] is False
        assert (
            await client.patch(path, headers=app_headers, json={"isCompliant": True})
        ).status_code == 400
        owner = {"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user}"}
        assert (
            await client.post(path + "/registeredOwners/$ref", headers=app_headers, json=owner)
        ).status_code == 403
        assert (
            await client.post(path + "/registeredOwners/$ref", headers=delegated, json=owner)
        ).status_code == 204
        assert (await client.delete(path, headers=app_headers)).status_code == 204
        assert (await client.get(path, headers=delegated)).status_code == 404


async def test_owner_expansion_is_snapshot_stable(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor_app, user):
        headers = await headers_for(
            database,
            settings,
            tenant,
            actor_app,
            None,
            {"Application.ReadWrite.All", "Directory.Read.All"},
        )
        headers["ConsistencyLevel"] = "eventual"
        apps = []
        for name in ["Expansion Alpha", "Expansion Bravo"]:
            created = await client.post(
                "/v1.0/applications", headers=headers, json={"displayName": name}
            )
            assert created.status_code == 201
            apps.append(created.json())
            result = await client.post(
                f"/v1.0/applications/{apps[-1]['id']}/owners/$ref",
                headers=headers,
                json={"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user}"},
            )
            assert result.status_code == 204, result.text
        response = await client.get(
            "/v1.0/applications",
            headers=headers,
            params={
                "$filter": "startswith(displayName,'Expansion')",
                "$select": "id,displayName",
                "$top": "1",
                "$expand": "owners($select=id,displayName)",
            },
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert str(user) in {owner["id"] for owner in page["value"][0]["owners"]}
        remaining = next(app for app in apps if app["id"] != page["value"][0]["id"])
        result = await client.delete(
            f"/v1.0/applications/{remaining['id']}/owners/{user}/$ref", headers=headers
        )
        assert result.status_code == 204
        next_page = await client.get(page["@odata.nextLink"], headers=headers)
        assert next_page.status_code == 200, next_page.text
        second = next_page.json()["value"][0]
        assert second["displayName"] == remaining["displayName"]
        assert str(user) in {owner["id"] for owner in second["owners"]}
        assert all(set(owner) == {"id", "displayName"} for owner in second["owners"])


async def test_item_owner_expansion_respects_limited_information(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor_app, user):
        broad = await headers_for(
            database,
            settings,
            tenant,
            actor_app,
            None,
            {"Application.ReadWrite.All", "Directory.Read.All"},
        )
        created = await client.post(
            "/v1.0/applications", headers=broad, json={"displayName": "Limited owner disclosure"}
        )
        assert created.status_code == 201
        path = "/v1.0/applications/" + created.json()["id"]
        added = await client.post(
            path + "/owners/$ref",
            headers=broad,
            json={"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user}"},
        )
        assert added.status_code == 204
        limited = await headers_for(
            database, settings, tenant, actor_app, None, {"Application.Read.All"}
        )
        response = await client.get(path + "?$expand=owners", headers=limited)
        assert response.status_code == 200, response.text
        owner = next(value for value in response.json()["owners"] if value["id"] == str(user))
        assert set(owner) == {"id", "@odata.type"}

import base64
from uuid import uuid4

from cryptography.hazmat.primitives import serialization

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.identity_support import certificate
from tests.integration.test_evidence_operations import drain_api
from tests.integration.test_identity_app_only import assertion
from tests.integration.test_security_mutations import create_operational_app


async def test_certificate_disclosure_and_failed_replacement_rollback(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        headers = await headers_for(
            database, settings, tenant, actor, None, {"Application.ReadWrite.All"}
        )
        app = await create_operational_app(client, tenant, headers)
        path = "/v1.0/applications/" + app["id"]
        key, cert = certificate()
        public = base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode()
        credential = {
            "keyId": str(uuid4()),
            "type": "AsymmetricX509Cert",
            "usage": "Verify",
            "key": public,
        }
        assert (
            await client.patch(path, headers=headers, json={"keyCredentials": [credential]})
        ).status_code == 204
        for endpoint in (path, "/v1.0/applications"):
            for selected in (False, True):
                params = {"$select": "id,keyCredentials"} if selected else {}
                response = await client.get(endpoint, headers=headers, params=params)
                assert response.status_code == 200, response.text
                obj = (
                    response.json()
                    if endpoint == path
                    else next(x for x in response.json()["value"] if x["id"] == app["id"])
                )
                assert obj["keyCredentials"][0]["key"] == (public if selected else None)
        private = key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        for bad in (
            dict(credential, key=base64.b64encode(private).decode()),
            dict(credential, keyId=str(uuid4()), key="not-valid-base64"),
        ):
            response = await client.patch(
                path,
                headers=headers,
                json={"displayName": "Must roll back", "keyCredentials": [credential, bad]},
            )
            assert response.status_code == 400, response.text
            assert (await client.get(path, headers=headers)).json()["displayName"] == app[
                "displayName"
            ]
            fields = {
                "grant_type": "client_credentials",
                "client_id": app["appId"],
                "scope": "https://graph.microsoft.com/.default",
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion(key, cert, tenant, app["appId"]),
            }
            assert (
                await client.post(f"/{tenant}/oauth2/v2.0/token", data=fields)
            ).status_code == 200


async def test_existing_foreign_owner_cannot_be_attached(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        other = await client.post(
            "/_mock/tenants",
            json={"slug": "foreign", "display_name": "Foreign", "domain": "foreign.test"},
        )
        assert other.status_code == 201, other.text
        foreign = await client.post(
            f"/_mock/tenants/{other.json()['id']}/identity/users",
            json={
                "upn": "outside@foreign.test",
                "display_name": "Outside",
                "password": "local-foreign-password",
            },
        )
        assert foreign.status_code == 201, foreign.text
        headers = await headers_for(
            database,
            settings,
            tenant,
            actor,
            None,
            {"Application.ReadWrite.All", "Directory.Read.All"},
        )
        app = await create_operational_app(client, tenant, headers)
        result = await client.post(
            f"/v1.0/applications/{app['id']}/owners/$ref",
            headers=headers,
            json={"@odata.id": f"https://graph.microsoft.com/v1.0/users/{foreign.json()['id']}"},
        )
        assert result.status_code == 404, result.text
        owners = (
            await client.get(f"/v1.0/applications/{app['id']}/owners", headers=headers)
        ).json()["value"]
        assert foreign.json()["id"] not in {x["id"] for x in owners}


async def test_security_audits_graph_export_reimport(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        base = f"/_mock/tenants/{tenant}/evidence"
        assert (
            await client.patch(base + "/settings", json={"delay_seconds": 0})
        ).status_code == 200
        headers = await headers_for(
            database,
            settings,
            tenant,
            actor,
            None,
            {"Application.ReadWrite.All", "AuditLog.Read.All"},
        )
        app = await create_operational_app(client, tenant, headers)
        path = "/v1.0/applications/" + app["id"]
        added = await client.post(path + "/addPassword", headers=headers, json={})
        assert added.status_code == 200, added.text
        await drain_api(client, tenant)
        response = await client.get("/v1.0/auditLogs/directoryAudits", headers=headers)
        assert response.status_code == 200, response.text
        graph = response.json()["value"]
        assert {"Add application", "Add service principal", "Add password to application"} <= {
            row["activityDisplayName"] for row in graph
        }
        response = await client.get(base + "/export?family=directoryAudit")
        assert response.status_code == 200, response.text
        exported = response.json()
        assert added.json()["secretText"] not in response.text
        assert "verifier" not in response.text and "privateKey" not in response.text
        assert {x["id"] for x in graph} == {x["payload"]["id"] for x in exported["records"]}
        other = await client.post(
            "/_mock/tenants",
            json={
                "slug": "restored-security",
                "display_name": "Restored",
                "domain": "restored.test",
            },
        )
        assert other.status_code == 201, other.text
        target = other.json()["id"]
        response = await client.post(f"/_mock/tenants/{target}/evidence/import", json=exported)
        assert response.status_code == 200, response.text
        await drain_api(client, target)
        restored = await client.get(
            f"/_mock/tenants/{target}/evidence/export?family=directoryAudit"
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["records"] == exported["records"]

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select

from mockazure.evidence.models import EvidenceSource
from mockazure.security.catalog import permission_id
from tests.integration.graph_support import graph_client, headers_for, registered_app
from tests.integration.test_evidence_operations import drain_api
from tests.integration.test_security_grants import WRITE, sp_ids


async def test_assignment_timestamp_is_per_permission(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        headers = await headers_for(database, settings, tenant, actor, None, WRITE)
        resource, sp = await sp_ids(client, headers, actor)
        started = datetime.now(UTC)
        response = await client.post(
            f"/v1.0/servicePrincipals/{sp}/appRoleAssignments",
            headers=headers,
            json={
                "principalId": sp,
                "resourceId": resource,
                "appRoleId": str(permission_id("User.Read.All", "application")),
            },
        )
        assert response.status_code == 201, response.text
        assert datetime.fromisoformat(response.json()["createdDateTime"]) >= started


async def test_delegated_principal_grants_snapshot_and_audit(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        base = f"/_mock/tenants/{tenant}/evidence"
        assert (
            await client.patch(base + "/settings", json={"delay_seconds": 0})
        ).status_code == 200
        headers = await headers_for(
            database, settings, tenant, actor, None, WRITE | {"AuditLog.Read.All"}
        )
        resource, sp = await sp_ids(client, headers, actor)
        original = []
        for consent in ("AllPrincipals", "Principal"):
            response = await client.post(
                "/v1.0/oauth2PermissionGrants",
                headers=headers,
                json={
                    "clientId": sp,
                    "resourceId": resource,
                    "consentType": consent,
                    "principalId": str(user) if consent == "Principal" else None,
                    "scope": "User.Read",
                },
            )
            assert response.status_code == 201, response.text
            original.append(response.json())
        path = f"/v1.0/servicePrincipals/{sp}/oauth2PermissionGrants"
        response = await client.get(path, headers=headers, params={"$top": 1})
        assert response.status_code == 200, response.text
        first = response.json()
        remaining = next(x for x in original if x["id"] != first["value"][0]["id"])
        result = await client.delete(
            "/v1.0/oauth2PermissionGrants/" + remaining["id"], headers=headers
        )
        assert result.status_code == 204
        second = await client.get(first["@odata.nextLink"], headers=headers)
        assert second.status_code == 200 and second.json()["value"] == [remaining], second.text
        other = await registered_app(client, tenant)
        other_headers = await headers_for(database, settings, tenant, other, None, WRITE)
        assert (
            await client.get(first["@odata.nextLink"], headers=other_headers)
        ).status_code == 400
        query = await client.get(
            "/v1.0/oauth2PermissionGrants",
            headers=headers,
            params={"$filter": f"clientId eq '{sp}'", "$select": "id,consentType"},
        )
        assert query.status_code == 200 and len(query.json()["value"]) == 1, query.text
        assert set(query.json()["value"][0]) == {"id", "consentType"}
        async with database.sessions() as session:
            before = await session.scalar(
                select(func.count())
                .select_from(EvidenceSource)
                .where(EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "security")
            )
        invalid = await client.patch(
            "/v1.0/oauth2PermissionGrants/" + first["value"][0]["id"],
            headers=headers,
            json={"scope": "User.Read not.a.permission"},
        )
        assert invalid.status_code == 400, invalid.text
        async with database.sessions() as session:
            after = await session.scalar(
                select(func.count())
                .select_from(EvidenceSource)
                .where(EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "security")
            )
        assert after == before
        await drain_api(client, tenant)
        exported = await client.get(base + "/export?family=directoryAudit")
        assert exported.status_code == 200, exported.text
        records = exported.json()["records"]
        assert (
            len(
                [
                    x
                    for x in records
                    if "oAuth2PermissionGrant" in x["payload"]["activityDisplayName"]
                ]
            )
            == 3
        )
        target = await client.post(
            "/_mock/tenants",
            json={
                "slug": "restored-grants",
                "display_name": "Restored grants",
                "domain": "restored.test",
            },
        )
        assert target.status_code == 201
        restored = await client.post(
            f"/_mock/tenants/{target.json()['id']}/evidence/import", json=exported.json()
        )
        assert restored.status_code == 200, restored.text


async def test_exchange_role_visible_in_resource_and_effective_assignments(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        created = await client.post(
            f"/_mock/tenants/{tenant}/identity/applications",
            json={
                "display_name": "Exchange client",
                "client_mode": "confidential",
                "required_permissions": [
                    {
                        "resource": "exchange",
                        "kind": "application",
                        "permissions": ["Exchange.ManageAsApp"],
                    }
                ],
            },
        )
        assert created.status_code == 201
        app = created.json()["client_id"]
        grant = await client.put(
            f"/_mock/tenants/{tenant}/identity/grants/{uuid4()}",
            json={
                "client_id": app,
                "resource": "exchange",
                "kind": "application",
                "permissions": ["Exchange.ManageAsApp"],
            },
        )
        assert grant.status_code == 200
        headers = await headers_for(database, settings, tenant, actor, None, WRITE)
        exo = await client.get(
            "/v1.0/servicePrincipals(appId='00000002-0000-0ff1-ce00-000000000000')", headers=headers
        )
        role_id = "dc50a0fb-09a3-484d-be87-e023b12c6440"
        assert any(x["id"] == role_id for x in exo.json()["appRoles"])
        assignments = await client.get(
            f"/v1.0/servicePrincipals(appId='{app}')/appRoleAssignments", headers=headers
        )
        assert assignments.status_code == 200, assignments.text
        assert assignments.json()["value"][0]["appRoleId"] == role_id
        assert assignments.json()["value"][0]["resourceId"] == exo.json()["id"]

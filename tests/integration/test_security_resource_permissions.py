from datetime import UTC, datetime

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_evidence_operations import drain_api
from tests.integration.test_security_grants import WRITE

EXCHANGE_APP = "00000002-0000-0ff1-ce00-000000000000"
EXCHANGE_ROLE = "dc50a0fb-09a3-484d-be87-e023b12c6440"


async def test_graph_configured_exchange_permission_and_audit(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        base = f"/_mock/tenants/{tenant}/evidence"
        assert (
            await client.patch(base + "/settings", json={"delay_seconds": 0})
        ).status_code == 200
        headers = await headers_for(database, settings, tenant, actor, None, WRITE)
        required = [
            {
                "resourceAppId": EXCHANGE_APP,
                "resourceAccess": [{"id": EXCHANGE_ROLE, "type": "Role"}],
            }
        ]
        created = await client.post(
            "/v1.0/applications",
            headers=headers,
            json={
                "displayName": "EXO configured through Graph",
                "requiredResourceAccess": required,
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["requiredResourceAccess"] == required
        app = created.json()
        sp = await client.post(
            "/v1.0/servicePrincipals", headers=headers, json={"appId": app["appId"]}
        )
        assert sp.status_code == 201
        exo = await client.get(f"/v1.0/servicePrincipals(appId='{EXCHANGE_APP}')", headers=headers)
        role = await client.post(
            f"/v1.0/servicePrincipals/{exo.json()['id']}/appRoleAssignedTo",
            headers=headers,
            json={
                "resourceId": exo.json()["id"],
                "principalId": sp.json()["id"],
                "appRoleId": EXCHANGE_ROLE,
            },
        )
        assert role.status_code == 201, role.text
        created_at = datetime.fromisoformat(role.json()["createdDateTime"])
        assert created_at <= datetime.now(UTC)
        deleted = await client.delete(
            f"/v1.0/servicePrincipals/{sp.json()['id']}/appRoleAssignments/{role.json()['id']}",
            headers=headers,
        )
        assert deleted.status_code == 204, deleted.text
        await drain_api(client, tenant)
        response = await client.get(base + "/export?family=directoryAudit")
        assert response.status_code == 200, response.text
        records = response.json()["records"]
        assert {"Add appRoleAssignment", "Remove appRoleAssignment"} <= {
            x["payload"]["activityDisplayName"] for x in records
        }


async def test_admin_configured_exchange_permission_visible(database, settings):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        created = await client.post(
            f"/_mock/tenants/{tenant}/identity/applications",
            json={
                "display_name": "Legacy exchange",
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
        headers = await headers_for(database, settings, tenant, actor, None, WRITE)
        obj = await client.get(
            f"/v1.0/applications(appId='{created.json()['client_id']}')", headers=headers
        )
        assert obj.status_code == 200
        assert obj.json()["requiredResourceAccess"] == [
            {
                "resourceAppId": EXCHANGE_APP,
                "resourceAccess": [{"id": EXCHANGE_ROLE, "type": "Role"}],
            }
        ]

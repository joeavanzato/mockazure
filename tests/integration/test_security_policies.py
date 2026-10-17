from uuid import uuid4

import httpx

from mockazure.admin.app import create_app
from mockazure.providers.graph.schema_reference import validate_payload
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_evidence_operations import drain_api
from tests.integration.test_graph_licensing import P1, PLAN1, seed_sku
from tests.policy_support import location_body, policy_body

BASE = "/v1.0/identity/conditionalAccess"
WRITE = {"Policy.Read.All", "Policy.ReadWrite.ConditionalAccess"}


async def test_policy_permissions_and_p1(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        # Tenant text labels do not substitute for an actual enabled service plan.
        assert (
            await client.post(BASE + "/policies", headers=headers, json=policy_body())
        ).status_code == 403
        await seed_sku(client, tenant, P1, PLAN1)
        for permissions in (
            {"Policy.ReadWrite.ConditionalAccess"},
            {"Policy.Read.All"},
            {"Directory.ReadWrite.All"},
        ):
            denied = await headers_for(database, settings, tenant, app, None, permissions)
            assert (
                await client.post(BASE + "/policies", headers=denied, json=policy_body())
            ).status_code == 403
        delegated = await headers_for(database, settings, tenant, app, user, WRITE, "delegated")
        assert (
            await client.post(BASE + "/policies", headers=delegated, json=policy_body())
        ).status_code == 403
        role_path = f"/_mock/tenants/{tenant}/identity/users/{user}/roles/"
        assert (await client.put(role_path + "Conditional Access Administrator")).status_code == 200
        result = await client.post(BASE + "/policies", headers=delegated, json=policy_body())
        assert result.status_code == 201, result.text
        alternate = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {
                "Application.Read.All",
                "Policy.ReadWrite.ConditionalAccess",
            },
        )
        assert (
            await client.patch(
                BASE + "/policies/" + result.json()["id"],
                headers=alternate,
                json={"state": "disabled"},
            )
        ).status_code == 204
        assert (await client.get(BASE + "/policies", headers=alternate)).status_code == 403
        assert (
            await client.delete(BASE + "/policies/" + result.json()["id"], headers=alternate)
        ).status_code == 403
        assert (
            await client.delete(BASE + "/policies/" + result.json()["id"], headers=headers)
        ).status_code == 204
        assert (
            await client.post(BASE + "/namedLocations", headers=alternate, json=location_body())
        ).status_code == 403


async def test_policy_crud_persistence_audit(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant, P1, PLAN1)
        evidence = f"/_mock/tenants/{tenant}/evidence"
        assert (
            await client.patch(evidence + "/settings", json={"delay_seconds": 0})
        ).status_code == 200
        headers = await headers_for(
            database, settings, tenant, app, None, WRITE | {"AuditLog.Read.All"}
        )
        location = await client.post(
            BASE + "/namedLocations", headers=headers, json=location_body()
        )
        assert location.status_code == 201, location.text
        loc = location.json()
        validate_payload(
            "v1.0",
            "ipNamedLocation",
            {k: v for k, v in loc.items() if k != "@odata.context"},
            partial=True,
        )
        body = policy_body()
        body["conditions"]["users"] = {"includeUsers": [str(user)]}
        body["conditions"]["locations"] = {
            "includeLocations": ["All"],
            "excludeLocations": [loc["id"]],
        }
        first = await client.post(BASE + "/policies", headers=headers, json=body)
        assert first.status_code == 201, first.text
        policy = first.json()
        path = BASE + "/policies/" + policy["id"]
        patch = await client.patch(
            path, headers=headers, json={"conditions": {"clientAppTypes": ["browser"]}}
        )
        assert patch.status_code == 204, patch.text
        current = (await client.get(path, headers=headers)).json()
        assert current["conditions"]["users"]["includeUsers"] == [str(user)]
        assert current["conditions"]["clientAppTypes"] == ["browser"]
        assert current["createdDateTime"] == policy["createdDateTime"]
        assert current["modifiedDateTime"] is not None
        bad = await client.patch(
            path,
            headers=headers,
            json={
                "state": "disabled",
                "sessionControls": {"persistentBrowser": {"isEnabled": True}},
            },
        )
        assert bad.status_code == 400
        assert (await client.get(path, headers=headers)).json()["state"] == "enabled"
        # Referenced locations cannot disappear and silently change the policy's meaning.
        assert (
            await client.delete(BASE + "/namedLocations/" + loc["id"], headers=headers)
        ).status_code == 409
        assert (
            await client.patch(
                BASE + "/namedLocations/" + loc["id"], headers=headers, json={"isTrusted": False}
            )
        ).status_code == 204
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings, database)),
            base_url="https://localhost:8443",
        ) as restarted:
            response = await restarted.get(path, headers=headers)
            assert response.status_code == 200 and response.json()["id"] == policy["id"]
        second = await client.post(
            BASE + "/policies", headers=headers, json=policy_body("disabled")
        )
        assert second.status_code == 201
        page = await client.get(BASE + "/policies?$top=1&$orderby=id&$count=true", headers=headers)
        assert page.status_code == 200, page.text
        assert page.json()["@odata.count"] == 2
        first_id = page.json()["value"][0]["id"]
        remaining = next(x for x in (policy["id"], second.json()["id"]) if x != first_id)
        assert (
            await client.delete(BASE + "/policies/" + remaining, headers=headers)
        ).status_code == 204
        next_page = await client.get(page.json()["@odata.nextLink"], headers=headers)
        assert next_page.status_code == 200 and next_page.json()["value"][0]["id"] == remaining
        assert (
            await client.delete(BASE + "/policies/" + first_id, headers=headers)
        ).status_code == 204
        assert (
            await client.delete(BASE + "/namedLocations/" + loc["id"], headers=headers)
        ).status_code == 204
        await drain_api(client, tenant)
        exported = await client.get(evidence + "/export?family=directoryAudit")
        assert exported.status_code == 200, exported.text
        records = exported.json()["records"]
        assert len(records) == 8
        assert {r["payload"]["category"] for r in records} == {"Policy"}
        assert {t["type"] for r in records for t in r["payload"]["targetResources"]} == {
            "conditionalAccessPolicy",
            "ipNamedLocation",
        }


async def test_policy_invalid_references_are_atomic(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant, P1, PLAN1)
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        for section, field in (
            ("users", "includeUsers"),
            ("users", "includeGroups"),
            ("applications", "includeApplications"),
            ("locations", "includeLocations"),
        ):
            body = policy_body()
            body["conditions"].setdefault(section, {})[field] = [str(uuid4())]
            assert (
                await client.post(BASE + "/policies", headers=headers, json=body)
            ).status_code == 400
        assert (await client.get(BASE + "/policies", headers=headers)).json()["value"] == []
        assert (
            await client.get(BASE + "/policies/" + str(uuid4()), headers=headers)
        ).status_code == 404
        assert (
            await client.get(BASE + "/policies?$expand=owners", headers=headers)
        ).status_code == 400

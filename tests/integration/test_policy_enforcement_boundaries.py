from sqlalchemy import delete

from mockazure.licensing.models import SubscribedSku
from tests.integration.graph_support import headers_for
from tests.integration.test_evidence_operations import drain_api
from tests.integration.test_policy_enforcement import (
    add_policy,
    authenticate,
    policy_client,
    redeem,
)
from tests.policy_support import policy_body


async def test_explicit_mfa_denial_keeps_policy_failure_evidence(database, settings):
    async with policy_client(database, settings) as (client, tenant, control, user, app, writer):
        await add_policy(client, writer, policy_body())
        result = await authenticate(client, tenant, app, mfa="deny")
        assert result.status_code == 303 and "error=access_denied" in result.headers["location"]
        await drain_api(client, tenant)
        reader = await headers_for(
            database, settings, tenant, control, None, {"AuditLog.Read.All", "Policy.Read.All"}
        )
        result = await client.get("/v1.0/auditLogs/signIns", headers=reader)
        assert result.status_code == 200
        record = result.json()["value"][0]
        assert record["status"]["errorCode"] == 50074
        assert record["conditionalAccessStatus"] == "failure"
        assert record["appliedConditionalAccessPolicies"][0]["result"] == "failure"


async def test_client_category_and_license_removal_cannot_bypass_enforcement(database, settings):
    async with policy_client(database, settings) as (client, tenant, control, user, app, writer):
        body = policy_body()
        body["conditions"]["clientAppTypes"] = ["browser"]
        path = await add_policy(client, writer, body)
        # This is the persisted public/native registration from the real PKCE fixture.
        tokens = await redeem(client, tenant, app, await authenticate(client, tenant, app))
        assert tokens.status_code == 200
        assert (
            await client.patch(
                path,
                headers=writer,
                json={"conditions": {"clientAppTypes": ["mobileAppsAndDesktopClients"]}},
            )
        ).status_code == 204
        refresh = {
            "grant_type": "refresh_token",
            "client_id": app["client_id"],
            "refresh_token": tokens.json()["refresh_token"],
        }
        assert (await client.post(f"/{tenant}/oauth2/v2.0/token", data=refresh)).status_code == 400
        completed = await redeem(
            client, tenant, app, await authenticate(client, tenant, app, mfa="complete")
        )
        assert completed.status_code == 200
        async with database.sessions.begin() as session:
            await session.execute(delete(SubscribedSku).where(SubscribedSku.tenant_id == tenant))
        refresh["refresh_token"] = completed.json()["refresh_token"]
        assert (await client.post(f"/{tenant}/oauth2/v2.0/token", data=refresh)).status_code == 400


async def test_report_only_mfa_never_forces_a_challenge(database, settings):
    async with policy_client(database, settings) as (client, tenant, control, user, app, writer):
        await add_policy(client, writer, policy_body("enabledForReportingButNotEnforced"))
        for choice in ("skip", "complete"):
            assert (
                await redeem(
                    client, tenant, app, await authenticate(client, tenant, app, mfa=choice)
                )
            ).status_code == 200
        await drain_api(client, tenant)
        reader = await headers_for(
            database, settings, tenant, control, None, {"AuditLog.Read.All", "Policy.Read.All"}
        )
        response = await client.get(
            "/v1.0/auditLogs/signIns", headers=reader | {"Prefer": "include-unknown-enum-members"}
        )
        assert response.status_code == 200
        values = response.json()["value"]
        assert len(values) == 2
        assert {v["conditionalAccessStatus"] for v in values} == {"notApplied"}
        assert {v["appliedConditionalAccessPolicies"][0]["result"] for v in values} == {
            "reportOnlyInterrupted",
            "reportOnlySuccess",
        }


async def test_delegated_policy_detail_requires_permission_and_reader_role(database, settings):
    async with policy_client(database, settings) as (client, tenant, control, user, app, writer):
        await add_policy(client, writer, policy_body())
        assert (await authenticate(client, tenant, app)).status_code == 400
        tokens = await redeem(
            client, tenant, app, await authenticate(client, tenant, app, mfa="complete")
        )
        assert tokens.status_code == 200
        await drain_api(client, tenant)
        role_path = f"/_mock/tenants/{tenant}/identity/users/{user['id']}/roles/"
        assert (await client.put(role_path + "Reports Reader")).status_code == 200
        reader = await headers_for(
            database,
            settings,
            tenant,
            control,
            user["id"],
            {"AuditLog.Read.All", "Policy.Read.All"},
            "delegated",
        )
        response = await client.get("/v1.0/auditLogs/signIns", headers=reader)
        assert response.status_code == 200
        assert len(response.json()["value"]) == 2
        assert all("appliedConditionalAccessPolicies" not in v for v in response.json()["value"])
        assert (await client.put(role_path + "Security Reader")).status_code == 200
        detailed = await client.get("/v1.0/auditLogs/signIns", headers=reader)
        assert detailed.status_code == 200
        values = detailed.json()["value"]
        failed = next(v for v in values if v["status"]["errorCode"] != 0)
        success = next(v for v in values if v["status"]["errorCode"] == 0)
        assert failed["status"]["errorCode"] == 50076
        assert failed["conditionalAccessStatus"] == "failure"
        assert success["conditionalAccessStatus"] == "success"
        assert success["appliedConditionalAccessPolicies"][0]["result"] == "success"
        no_policy_permission = await headers_for(
            database, settings, tenant, control, user["id"], {"AuditLog.Read.All"}, "delegated"
        )
        hidden = await client.get("/v1.0/auditLogs/signIns", headers=no_policy_permission)
        assert all("appliedConditionalAccessPolicies" not in v for v in hidden.json()["value"])


async def test_matching_block_overrides_mfa_and_nested_group_is_effective(database, settings):
    async with policy_client(database, settings) as (client, tenant, control, user, app, writer):
        directory = await headers_for(
            database, settings, tenant, control, None, {"Group.ReadWrite.All"}
        )
        groups = []
        for name in ("inner", "outer"):
            response = await client.post(
                "/v1.0/groups",
                headers=directory,
                json={
                    "displayName": name,
                    "mailEnabled": False,
                    "mailNickname": name,
                    "securityEnabled": True,
                },
            )
            assert response.status_code == 201
            groups.append(response.json()["id"])
        for parent, child in ((groups[0], user["id"]), (groups[1], groups[0])):
            response = await client.post(
                f"/v1.0/groups/{parent}/members/$ref",
                headers=directory,
                json={"@odata.id": f"https://localhost:8443/v1.0/directoryObjects/{child}"},
            )
            assert response.status_code == 204, response.text
        await add_policy(client, writer, policy_body())
        block = policy_body(control="block")
        block["conditions"]["users"] = {"includeGroups": [groups[1]]}
        path = await add_policy(client, writer, block)
        assert (await authenticate(client, tenant, app, mfa="complete")).status_code == 400
        assert (
            await client.patch(
                path, headers=writer, json={"conditions": {"users": {"excludeUsers": [user["id"]]}}}
            )
        ).status_code == 204
        assert (
            await redeem(
                client, tenant, app, await authenticate(client, tenant, app, mfa="complete")
            )
        ).status_code == 200

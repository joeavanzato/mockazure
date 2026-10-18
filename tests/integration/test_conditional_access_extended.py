from tests.integration.test_policy_enforcement import (
    add_policy,
    authenticate,
    policy_client,
    redeem,
)
from tests.policy_support import policy_body


async def test_compliance_unknown_and_or_report_only(database, settings):
    async with policy_client(database, settings) as (client, tenant, control, user, app, writer):
        body = policy_body()
        body["grantControls"] = {"operator": "AND", "builtInControls": ["mfa", "compliantDevice"]}
        path = await add_policy(client, writer, body)
        assert (
            await authenticate(
                client,
                tenant,
                app,
                mfa="complete",
                request_headers={"X-Device-Compliant": "true", "X-MFA": "completed"},
            )
        ).status_code == 400
        assert (
            await client.patch(
                path, headers=writer, json={"state": "enabledForReportingButNotEnforced"}
            )
        ).status_code == 204
        assert (
            await redeem(client, tenant, app, await authenticate(client, tenant, app))
        ).status_code == 200


async def test_extended_policy_writes_are_v1_only(database, settings):
    async with policy_client(database, settings) as (client, tenant, control, user, app, writer):
        body = policy_body()
        body["grantControls"] = {"operator": "OR", "builtInControls": ["compliantDevice"]}
        response = await client.post(
            "/beta/identity/conditionalAccess/policies", headers=writer, json=body
        )
        assert response.status_code == 501, response.text

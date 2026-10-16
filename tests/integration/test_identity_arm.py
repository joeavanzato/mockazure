from uuid import uuid4

from tests.integration.identity_support import identity_client


async def test_arm_discovery_uses_persisted_subscription_and_rbac(database, settings):
    async with identity_client(database, settings) as (client, tenant, client_id, secret, *_):
        subscription = await client.post(
            f"/_mock/tenants/{tenant}/subscriptions", json={"display_name": "Evidence lab"}
        )
        assert subscription.status_code == 201, subscription.text
        subscription_id = subscription.json()["id"]
        token = (
            await client.post(
                f"/{tenant}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": secret["secret"],
                    "scope": "https://management.azure.com/.default",
                },
            )
        ).json()
        headers = {"Authorization": "Bearer " + token["access_token"]}
        endpoint = "/subscriptions?api-version=2021-01-01"
        assert (await client.get(endpoint, headers=headers)).json()["value"] == []
        assignment = (
            f"/_mock/tenants/{tenant}/subscriptions/{subscription_id}/assignments/{uuid4()}"
        )
        result = await client.put(assignment, json={"client_id": client_id})
        assert result.status_code == 200, result.text
        rows = (await client.get(endpoint, headers=headers)).json()["value"]
        assert len(rows) == 1 and rows[0]["subscriptionId"] == subscription_id
        assert rows[0]["tenantId"] == tenant and rows[0]["displayName"] == "Evidence lab"
        assert (
            await client.get("/subscriptions?api-version=9999", headers=headers)
        ).status_code == 400
        assert (await client.delete(assignment)).status_code == 200
        assert (await client.get(endpoint, headers=headers)).json()["value"] == []
        assert (
            await client.get(endpoint, headers={"Authorization": "Bearer invalid"})
        ).status_code == 401

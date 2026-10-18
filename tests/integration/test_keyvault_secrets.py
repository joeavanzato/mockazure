"""Key Vault secret versions use service-native schema and current vault authority."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from mockazure.providers.arm.models import RoleAssignment
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_arm_authorization import assign
from tests.integration.test_cloud_resource_inventory import VERSIONS, create, prepare

ARGS = {"api-version": "2025-07-01"}


async def provision_vault(client, database, settings, tenant, app, *, policies=False):
    _, admin, group, principal, arm = await prepare(client, database, settings, tenant, app)
    path = group + "/providers/Microsoft.KeyVault/vaults/local-vault"
    policy = {
        "tenantId": str(tenant),
        "objectId": str(principal),
        "permissions": {"secrets": ["all"]},
    }
    vault = await create(
        client,
        path,
        {
            "location": "eastus",
            "properties": {
                "tenantId": str(tenant),
                "sku": {"family": "A", "name": "standard"},
                "enableRbacAuthorization": not policies,
                "accessPolicies": [policy] if policies else [],
            },
        },
        arm,
        VERSIONS["Microsoft.KeyVault"],
    )
    head = await headers_for(
        database, settings, tenant, app, None, [], audience="https://vault.azure.net"
    )
    return path, admin, principal, arm, vault["properties"]["vaultUri"], head, policy


async def test_secret_versions_and_attributes(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, admin, principal, arm, base, head, policy = await provision_vault(
            client, database, settings, tenant, app
        )
        await assign(client, admin, principal, path, "Key Vault Secrets Officer")
        url = base + "secrets/evidence-secret"
        first = await client.put(
            url,
            params=ARGS,
            headers=head,
            json={
                "value": "first-secret",
                "contentType": "text/plain",
                "tags": {"purpose": "testing"},
            },
        )
        assert first.status_code == 200, first.text
        a = first.json()
        assert a["value"] == "first-secret" and "/secrets/evidence-secret/" in a["id"]
        assert a["attributes"]["enabled"] and a["attributes"]["recoverableDays"] == 90
        second = await client.put(url, params=ARGS, headers=head, json={"value": "second-secret"})
        assert second.status_code == 200
        b = second.json()
        assert a["id"] != b["id"]
        assert (await client.get(a["id"], params=ARGS, headers=head)).json()[
            "value"
        ] == "first-secret"
        assert (await client.get(url, params=ARGS, headers=head)).json()["id"] == b["id"]
        listing = await client.get(
            base + "secrets", params=ARGS | {"maxresults": "1"}, headers=head
        )
        assert listing.status_code == 200 and len(listing.json()["value"]) == 1
        assert "value" not in listing.json()["value"][0]
        versions = await client.get(
            url + "/versions", params=ARGS | {"maxresults": "1"}, headers=head
        )
        assert versions.status_code == 200
        body = versions.json()
        assert "value" not in body["value"][0] and body["nextLink"].startswith(base)
        page = await client.get(body["nextLink"], headers=head)
        assert page.status_code == 200 and len(page.json()["value"]) == 1
        # Published GET semantics allow reads outside temporal eligibility attributes.
        now = datetime.now(UTC)
        patch = await client.patch(
            b["id"],
            params=ARGS,
            headers=head,
            json={
                "attributes": {
                    "nbf": int((now + timedelta(days=1)).timestamp()),
                    "exp": int((now - timedelta(days=1)).timestamp()),
                }
            },
        )
        assert patch.status_code == 200
        assert (await client.get(url, params=ARGS, headers=head)).json()["value"] == "second-secret"
        assert (
            await client.patch(
                b["id"], params=ARGS, headers=head, json={"attributes": {"enabled": False}}
            )
        ).status_code == 200
        disabled = await client.get(url, params=ARGS, headers=head)
        assert disabled.status_code == 403 and disabled.json()["error"]["code"] == "Forbidden"
        assert (await client.get(a["id"], params=ARGS, headers=head)).status_code == 200
        await database.engine.dispose()
        assert (await client.get(a["id"], params=ARGS, headers=head)).json()[
            "value"
        ] == "first-secret"


async def test_vault_auth_mode_switch(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, admin, principal, arm, base, head, policy = await provision_vault(
            client, database, settings, tenant, app
        )
        url = base + "secrets/policy-secret"
        denied = await client.put(url, params=ARGS, headers=head, json={"value": "hidden"})
        assert denied.status_code == 403, denied.text
        await assign(client, admin, principal, path, "Key Vault Secrets Officer")
        assert (
            await client.put(url, params=ARGS, headers=head, json={"value": "hidden"})
        ).status_code == 200
        assert (await client.get(url, params=ARGS, headers=arm)).status_code == 401
        # Contributor cannot change the permission model without role-assignment authority.
        await assign(client, admin, principal, path, "Owner")
        changed = await client.patch(
            path,
            params={"api-version": VERSIONS["Microsoft.KeyVault"]},
            headers=arm,
            json={"properties": {"enableRbacAuthorization": False, "accessPolicies": []}},
        )
        assert changed.status_code == 200, changed.text
        assert (await client.get(url, params=ARGS, headers=head)).status_code == 403
        added = await client.patch(
            path,
            params={"api-version": VERSIONS["Microsoft.KeyVault"]},
            headers=arm,
            json={"properties": {"accessPolicies": [policy]}},
        )
        assert added.status_code == 200
        assert (await client.get(url, params=ARGS, headers=head)).status_code == 200
        async with database.sessions.begin() as session:
            await session.execute(
                delete(RoleAssignment).where(
                    RoleAssignment.tenant_id == tenant,
                    RoleAssignment.scope_key == path.lower(),
                    RoleAssignment.role_id == "b86a8fe4-44ce-4948-aee5-eccb2c155cd7",
                )
            )
        switched = await client.patch(
            path,
            params={"api-version": VERSIONS["Microsoft.KeyVault"]},
            headers=arm,
            json={"properties": {"enableRbacAuthorization": True}},
        )
        assert switched.status_code == 200
        assert (await client.get(url, params=ARGS, headers=head)).status_code == 403

"""Related provider resources must share canonical inventory and current authority."""

from uuid import uuid4

from tests.integration.graph_support import graph_client
from tests.integration.test_arm_authorization import assign
from tests.integration.test_cloud_provider_registry import context

VERSIONS = {
    "Microsoft.Compute": "2025-04-01",
    "Microsoft.Network": "2025-03-01",
    "Microsoft.KeyVault": "2025-05-01",
    "Microsoft.OperationalInsights": "2025-02-01",
}


async def prepare(client, database, settings, tenant, app):
    sub, base, group, principal, headers = await context(client, database, settings, tenant, app)
    await assign(client, base, principal, f"/subscriptions/{sub}", "Contributor")
    for namespace in VERSIONS:
        response = await client.post(
            f"/subscriptions/{sub}/providers/{namespace}/register",
            params={"api-version": "2021-04-01"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
    return sub, base, group, principal, headers


async def create(client, path, value, headers, version):
    response = await client.put(path, params={"api-version": version}, headers=headers, json=value)
    assert response.status_code in {200, 201}, response.text
    result = response.json()
    assert result["id"].lower() == path.lower()
    assert result["properties"]["provisioningState"] == "Succeeded"
    fetched = await client.get(path, params={"api-version": version}, headers=headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == result
    return result


async def complete_delete(database, tenant):
    from datetime import UTC, datetime

    from mockazure.jobs.dispatch import dispatch_job
    from mockazure.jobs.repository import claim_job

    now = datetime.now(UTC)
    async with database.sessions.begin() as session:
        claim = await claim_job(
            session,
            owner=uuid4(),
            now=now,
            lease_seconds=30,
            tenant_id=tenant,
            kind="arm.provider.operation",
        )
    assert claim is not None
    assert (await dispatch_job(database, claim, now)).state == "succeeded"


async def test_related_compute_network_inventory(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        net = group + "/providers/Microsoft.Network/"
        compute = group + "/providers/Microsoft.Compute/"
        version = VERSIONS["Microsoft.Network"]
        vnet = await create(
            client,
            net + "virtualNetworks/network",
            {
                "location": "eastus",
                "properties": {"addressSpace": {"addressPrefixes": ["10.42.0.0/16"]}},
            },
            headers,
            version,
        )
        nsg = await create(
            client, net + "networkSecurityGroups/security", {"location": "eastus"}, headers, version
        )
        rule = await create(
            client,
            nsg["id"] + "/securityRules/https",
            {
                "properties": {
                    "priority": 100,
                    "direction": "Inbound",
                    "access": "Allow",
                    "protocol": "Tcp",
                    "sourcePortRange": "*",
                    "destinationPortRange": "443",
                    "sourceAddressPrefix": "*",
                    "destinationAddressPrefix": "*",
                }
            },
            headers,
            version,
        )
        subnet = await create(
            client,
            vnet["id"] + "/subnets/default",
            {
                "properties": {
                    "addressPrefix": "10.42.1.0/24",
                    "networkSecurityGroup": {"id": nsg["id"]},
                }
            },
            headers,
            version,
        )
        public = await create(
            client,
            net + "publicIPAddresses/public",
            {
                "location": "eastus",
                "sku": {"name": "Standard"},
                "properties": {"publicIPAllocationMethod": "Static"},
            },
            headers,
            version,
        )
        nic = await create(
            client,
            net + "networkInterfaces/nic",
            {
                "location": "eastus",
                "properties": {
                    "networkSecurityGroup": {"id": nsg["id"]},
                    "ipConfigurations": [
                        {
                            "name": "primary",
                            "properties": {
                                "subnet": {"id": subnet["id"]},
                                "publicIPAddress": {"id": public["id"]},
                                "privateIPAllocationMethod": "Dynamic",
                            },
                        }
                    ],
                },
            },
            headers,
            version,
        )
        disk = await create(
            client,
            compute + "disks/disk",
            {
                "location": "eastus",
                "sku": {"name": "Standard_LRS"},
                "properties": {
                    "creationData": {"createOption": "Empty"},
                    "diskSizeGB": 32,
                    "osType": "Linux",
                },
            },
            headers,
            "2025-01-02",
        )
        vm = await create(
            client,
            compute + "virtualMachines/operational",
            {
                "location": "eastus",
                "properties": {
                    "hardwareProfile": {"vmSize": "Standard_B1s"},
                    "networkProfile": {"networkInterfaces": [{"id": nic["id"]}]},
                    "storageProfile": {
                        "osDisk": {
                            "createOption": "Attach",
                            "osType": "Linux",
                            "managedDisk": {"id": disk["id"]},
                        }
                    },
                },
            },
            headers,
            VERSIONS["Microsoft.Compute"],
        )
        extension = await create(
            client,
            vm["id"] + "/extensions/inventory",
            {
                "location": "eastus",
                "properties": {
                    "publisher": "Microsoft.Azure.Extensions",
                    "type": "CustomScript",
                    "typeHandlerVersion": "2.1",
                    "settings": {"commandToExecute": "must-not-execute"},
                },
            },
            headers,
            VERSIONS["Microsoft.Compute"],
        )
        assert vm["properties"]["networkProfile"]["networkInterfaces"][0]["id"] == nic["id"]
        inventory = await client.get(
            f"/subscriptions/{sub}/resources", params={"api-version": "2021-04-01"}, headers=headers
        )
        ids = {value["id"] for value in inventory.json()["value"]}
        assert {r["id"] for r in (vnet, nsg, rule, subnet, public, nic, disk, vm, extension)} <= ids
        parent = (
            await client.get(vnet["id"], params={"api-version": version}, headers=headers)
        ).json()
        assert parent["properties"]["subnets"][0]["id"] == subnet["id"]
        listed = await client.get(
            net + "networkInterfaces", params={"api-version": version}, headers=headers
        )
        assert listed.status_code == 200 and nic["id"] in {r["id"] for r in listed.json()["value"]}
        denied_delete = await client.delete(
            nic["id"], params={"api-version": version}, headers=headers
        )
        assert denied_delete.status_code == 409, denied_delete.text
        removed = await client.delete(
            vm["id"], params={"api-version": VERSIONS["Microsoft.Compute"]}, headers=headers
        )
        assert removed.status_code == 202, removed.text
        await complete_delete(database, tenant)
        assert (
            await client.get(
                extension["id"],
                params={"api-version": VERSIONS["Microsoft.Compute"]},
                headers=headers,
            )
        ).status_code == 404


async def test_vault_workspace_authority(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        vault = await create(
            client,
            group + "/providers/Microsoft.KeyVault/vaults/local-vault",
            {
                "location": "eastus",
                "properties": {
                    "tenantId": str(tenant),
                    "sku": {"family": "A", "name": "standard"},
                    "enableRbacAuthorization": True,
                    "enableSoftDelete": True,
                    "softDeleteRetentionInDays": 90,
                    "enablePurgeProtection": True,
                },
            },
            headers,
            VERSIONS["Microsoft.KeyVault"],
        )
        assert vault["properties"]["vaultUri"].startswith(settings.endpoints.keyvault + "/")
        workspace = await create(
            client,
            group + "/providers/Microsoft.OperationalInsights/workspaces/evidence",
            {
                "location": "eastus",
                "properties": {"sku": {"name": "PerGB2018"}, "retentionInDays": 30},
            },
            headers,
            VERSIONS["Microsoft.OperationalInsights"],
        )
        from uuid import UUID

        UUID(workspace["properties"]["customerId"])
        changed = await client.patch(
            workspace["id"],
            params={"api-version": VERSIONS["Microsoft.OperationalInsights"]},
            headers=headers,
            json={"properties": {"retentionInDays": 60}, "tags": {"purpose": "collection"}},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["properties"]["retentionInDays"] == 60
        assert changed.json()["properties"]["customerId"] == workspace["properties"]["customerId"]
        from mockazure.keyvault.models import Vault
        from mockazure.monitor.workspaces import Workspace

        async with database.sessions() as session:
            assert await session.get(Vault, (tenant, vault["id"].lower())) is not None
            assert await session.get(Workspace, (tenant, workspace["id"].lower())) is not None


async def test_resource_references_and_profiles_fail_atomically(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        path = group + "/providers/Microsoft.Network/networkInterfaces/invalid"
        target = (
            group.replace(str(sub), str(uuid4()))
            + "/providers/Microsoft.Network/virtualNetworks/foreign/subnets/default"
        )
        payload = {
            "location": "eastus",
            "properties": {
                "ipConfigurations": [{"name": "primary", "properties": {"subnet": {"id": target}}}]
            },
        }
        before = (
            await client.get(
                f"/subscriptions/{sub}/resources",
                params={"api-version": "2021-04-01"},
                headers=headers,
            )
        ).json()
        response = await client.put(
            path,
            params={"api-version": VERSIONS["Microsoft.Network"]},
            headers=headers,
            json=payload,
        )
        assert response.status_code == 400, response.text
        after = (
            await client.get(
                f"/subscriptions/{sub}/resources",
                params={"api-version": "2021-04-01"},
                headers=headers,
            )
        ).json()
        assert after == before
        vnet = await create(
            client,
            group + "/providers/Microsoft.Network/virtualNetworks/gone",
            {
                "location": "eastus",
                "properties": {"addressSpace": {"addressPrefixes": ["10.0.0.0/16"]}},
            },
            headers,
            VERSIONS["Microsoft.Network"],
        )
        assert (
            await client.delete(
                vnet["id"], params={"api-version": VERSIONS["Microsoft.Network"]}, headers=headers
            )
        ).status_code == 202
        await complete_delete(database, tenant)
        response = await client.put(
            vnet["id"] + "/subnets/child",
            params={"api-version": VERSIONS["Microsoft.Network"]},
            headers=headers,
            json={"properties": {"addressPrefix": "10.0.1.0/24"}},
        )
        assert response.status_code in (400, 404), response.text
        for body in ({"location": "invented"}, {"location": "eastus", "sku": {"name": "Invented"}}):
            response = await client.put(
                group + "/providers/Microsoft.Network/publicIPAddresses/invalid",
                params={"api-version": VERSIONS["Microsoft.Network"]},
                headers=headers,
                json=body,
            )
            assert response.status_code == 400, response.text


async def test_linked_resource_permissions_are_current_and_scoped(database, settings):
    from sqlalchemy import select

    from mockazure.identity.models import ServicePrincipal
    from tests.integration.graph_support import registered_app
    from tests.integration.test_arm_inventory import access

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        version = VERSIONS["Microsoft.Network"]
        vnet = await create(
            client,
            group + "/providers/Microsoft.Network/virtualNetworks/shared",
            {
                "location": "eastus",
                "properties": {"addressSpace": {"addressPrefixes": ["10.1.0.0/16"]}},
            },
            headers,
            version,
        )
        subnet = await create(
            client,
            vnet["id"] + "/subnets/default",
            {"properties": {"addressPrefix": "10.1.0.0/24"}},
            headers,
            version,
        )
        other = f"/subscriptions/{sub}/resourceGroups/other"
        assert (
            await client.put(
                other,
                params={"api-version": "2021-04-01"},
                headers=headers,
                json={"location": "eastus"},
            )
        ).status_code == 201
        restricted = await registered_app(client, tenant)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == restricted
                )
            )
        await assign(client, base, sp, other, "Contributor")
        restricted_headers = await access(database, settings, tenant, restricted)
        path = other + "/providers/Microsoft.Network/networkInterfaces/nic"
        payload = {
            "location": "eastus",
            "properties": {
                "ipConfigurations": [
                    {"name": "primary", "properties": {"subnet": {"id": subnet["id"]}}}
                ]
            },
        }
        denied = await client.put(
            path, params={"api-version": version}, headers=restricted_headers, json=payload
        )
        assert denied.status_code == 403, denied.text
        # A read grant is insufficient to join a subnet.
        grant = await assign(client, base, sp, subnet["id"], "Reader")
        assert (
            await client.put(
                path, params={"api-version": version}, headers=restricted_headers, json=payload
            )
        ).status_code == 403
        await client.delete(base + "/role-assignments/" + grant["name"])
        grant = await assign(client, base, sp, subnet["id"], "Contributor")
        assert (
            await client.put(
                path, params={"api-version": version}, headers=restricted_headers, json=payload
            )
        ).status_code in (200, 201)
        await client.delete(base + "/role-assignments/" + grant["name"])
        assert (
            await client.put(
                path, params={"api-version": version}, headers=restricted_headers, json=payload
            )
        ).status_code == 403


async def test_network_parent_updates_preserve_child_geometry(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        version = VERSIONS["Microsoft.Network"]
        path = group + "/providers/Microsoft.Network/virtualNetworks/geometry"
        original = {
            "location": "eastus",
            "properties": {"addressSpace": {"addressPrefixes": ["10.20.0.0/16"]}},
        }
        vnet = await create(client, path, original, headers, version)
        await create(
            client,
            path + "/subnets/default",
            {"properties": {"addressPrefix": "10.20.1.0/24"}},
            headers,
            version,
        )
        invalid = {
            "location": "eastus",
            "properties": {"addressSpace": {"addressPrefixes": ["10.21.0.0/16"]}},
        }
        failed = await client.put(
            path, params={"api-version": version}, headers=headers, json=invalid
        )
        assert failed.status_code == 400, failed.text
        current = (await client.get(path, params={"api-version": version}, headers=headers)).json()
        assert current["properties"]["addressSpace"] == vnet["properties"]["addressSpace"]
        wrong_region = group + "/providers/Microsoft.Network/networkInterfaces/wrongregion"
        response = await client.put(
            wrong_region,
            params={"api-version": version},
            headers=headers,
            json={
                "location": "westus2",
                "properties": {
                    "ipConfigurations": [
                        {
                            "name": "primary",
                            "properties": {"subnet": {"id": path + "/subnets/default"}},
                        }
                    ]
                },
            },
        )
        assert response.status_code == 400, response.text


async def test_provider_fixture_restore_preserves_relationships_and_workspace_identity(
    database, settings
):
    from datetime import UTC, datetime

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        version = VERSIONS["Microsoft.Network"]
        path = group + "/providers/Microsoft.Network/virtualNetworks/fixture"
        await create(
            client,
            path,
            {
                "location": "eastus",
                "properties": {"addressSpace": {"addressPrefixes": ["10.99.0.0/16"]}},
            },
            headers,
            version,
        )
        await create(
            client,
            path + "/subnets/default",
            {"properties": {"addressPrefix": "10.99.1.0/24"}},
            headers,
            version,
        )
        workspace = await create(
            client,
            group + "/providers/Microsoft.OperationalInsights/workspaces/restored",
            {"location": "eastus"},
            headers,
            VERSIONS["Microsoft.OperationalInsights"],
        )
        local = f"/_mock/tenants/{tenant}/cloud/resourceState"
        exported = await client.get(local)
        assert exported.status_code == 200, exported.text
        state = exported.json()
        assert len(state["resources"]) == 3
        from copy import deepcopy

        bad = deepcopy(state)
        bad["resources"][0]["id"] = bad["resources"][0]["id"].replace(str(sub), str(uuid4()))
        assert (await client.put(local, json=bad)).status_code == 422
        assert (await client.get(local)).json() == state
        assert (await client.put(local, json={"resources": []})).status_code == 200
        assert (
            await client.get(path, params={"api-version": version}, headers=headers)
        ).status_code == 404
        # Intentionally reverse order: parent/reference resolution cannot depend on JSON ordering.
        state["resources"].reverse()
        restored = await client.put(local, json=state)
        assert restored.status_code == 200, restored.text
        parent = (await client.get(path, params={"api-version": version}, headers=headers)).json()
        assert parent["properties"]["subnets"][0]["id"] == path + "/subnets/default"
        current = (
            await client.get(
                workspace["id"],
                params={"api-version": VERSIONS["Microsoft.OperationalInsights"]},
                headers=headers,
            )
        ).json()
        assert current["properties"]["customerId"] == workspace["properties"]["customerId"]
        from mockazure.providers.arm.resource_lifecycle import prune_provider_resources

        async with database.sessions.begin() as session:
            assert await prune_provider_resources(session, tenant, datetime.now(UTC), 10) == 0


async def test_provider_writes_are_atomic_idempotent_and_not_import_overwritable(
    database, settings
):
    from unittest.mock import patch

    from sqlalchemy import func, select

    from mockazure.evidence.models import EvidenceSource

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        path = group + "/providers/Microsoft.OperationalInsights/workspaces/atomic"
        args = {"api-version": VERSIONS["Microsoft.OperationalInsights"]}
        value = {"location": "eastus"}
        await create(client, path, value, headers, args["api-version"])
        async with database.sessions() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(EvidenceSource)
                .where(EvidenceSource.tenant_id == tenant)
            )
        assert (await client.put(path, params=args, headers=headers, json=value)).status_code == 200
        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceSource)
                    .where(EvidenceSource.tenant_id == tenant)
                )
                == count
            )
        before = (await client.get(path, params=args, headers=headers)).json()

        async def fail(*args, **kwargs):
            raise ValueError("injected publication failure")

        with patch("mockazure.providers.arm.resource_handlers.append_service_event", fail):
            response = await client.patch(
                path, params=args, headers=headers, json={"tags": {"rollback": "required"}}
            )
        assert response.status_code == 400
        assert (await client.get(path, params=args, headers=headers)).json() == before
        imported = await client.post(
            base + "/resources",
            json={
                "resources": [
                    {
                        "id": path,
                        "name": "atomic",
                        "type": "Microsoft.OperationalInsights/workspaces",
                        "location": "eastus",
                        "properties": {},
                    }
                ]
            },
        )
        assert imported.status_code in (400, 409, 422)
        assert (await client.get(path, params=args, headers=headers)).json() == before
        for payload in (
            {"properties": {"customerId": str(uuid4())}},
            {"properties": {"sku": {"name": "Invented"}}},
        ):
            assert (
                await client.patch(path, params=args, headers=headers, json=payload)
            ).status_code == 400


async def test_vault_policy_mode_changes_need_role_assignment_authority(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        path = group + "/providers/Microsoft.KeyVault/vaults/policy-vault"
        version = VERSIONS["Microsoft.KeyVault"]
        await create(
            client,
            path,
            {
                "location": "eastus",
                "properties": {
                    "tenantId": str(tenant),
                    "sku": {"family": "A", "name": "standard"},
                    "accessPolicies": [
                        {
                            "tenantId": str(tenant),
                            "objectId": str(principal),
                            "permissions": {"secrets": ["get", "list"]},
                        }
                    ],
                },
            },
            headers,
            version,
        )
        payload = {"properties": {"enableRbacAuthorization": True}}
        assert (
            await client.patch(path, params={"api-version": version}, headers=headers, json=payload)
        ).status_code == 403
        await assign(client, base, principal, path, "Owner")
        changed = await client.patch(
            path, params={"api-version": version}, headers=headers, json=payload
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["properties"]["enableRbacAuthorization"] is True
        for props in (
            {"softDeleteRetentionInDays": 7},
            {"tenantId": str(uuid4())},
            {"enableSoftDelete": False},
        ):
            assert (
                await client.patch(
                    path,
                    params={"api-version": version},
                    headers=headers,
                    json={"properties": props},
                )
            ).status_code == 400


async def test_cloud_resource_migration_preserves_storage_keys(database, settings):
    from alembic import command
    from sqlalchemy import create_engine, text

    from mockazure.db.migrations import configuration, migrate
    from tests.integration.test_storage_account_management import (
        ARGS,
        CREATE,
    )
    from tests.integration.test_storage_account_management import (
        setup as storage_setup,
    )

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await storage_setup(
            client, database, settings, tenant, app
        )
        path = group + "/providers/Microsoft.Storage/storageAccounts/upgradeaccount"
        assert (
            await client.put(path, params=ARGS, headers=headers, json=CREATE)
        ).status_code == 200
        keys = (await client.post(path + "/listKeys", params=ARGS, headers=headers)).json()
        engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
        try:
            with engine.begin() as connection:
                config = configuration()
                config.attributes["connection"] = connection
                command.downgrade(config, "058")
                assert connection.scalar(text("SELECT to_regclass('cloud_resources')")) is None
            migrate(settings.database_url.get_secret_value())
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "063"
        finally:
            engine.dispose()
        assert (await client.post(path + "/listKeys", params=ARGS, headers=headers)).json() == keys


async def test_provider_paging_rechecks_authority_and_revision(database, settings):
    from sqlalchemy import select

    from mockazure.providers.arm.models import RoleAssignment

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        collection = group + "/providers/Microsoft.Network/networkSecurityGroups"
        state = {
            "resources": [
                {"id": collection + f"/paged{index:03d}", "configuration": {"location": "eastus"}}
                for index in range(101)
            ]
        }
        response = await client.put(f"/_mock/tenants/{tenant}/cloud/resourceState", json=state)
        assert response.status_code == 200, response.text
        args = {"api-version": VERSIONS["Microsoft.Network"]}
        first = (await client.get(collection, params=args, headers=headers)).json()
        assert len(first["value"]) == 100 and "nextLink" in first
        second = (await client.get(first["nextLink"], headers=headers)).json()
        assert len(second["value"]) == 1 and "nextLink" not in second
        assert len({r["id"] for r in first["value"] + second["value"]}) == 101
        changed = await client.patch(
            collection + "/paged000",
            params=args,
            headers=headers,
            json={"tags": {"changed": "yes"}},
        )
        assert changed.status_code == 200, changed.text
        assert (await client.get(first["nextLink"], headers=headers)).status_code == 400
        fresh = (await client.get(collection, params=args, headers=headers)).json()
        async with database.sessions() as session:
            grants = list(
                await session.scalars(
                    select(RoleAssignment.id).where(
                        RoleAssignment.tenant_id == tenant, RoleAssignment.client_id == app
                    )
                )
            )
        for identifier in grants:
            await client.delete(base + "/role-assignments/" + str(identifier))
        assert (await client.get(fresh["nextLink"], headers=headers)).status_code == 403


async def test_vault_published_all_secret_permission(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        response = await client.put(
            group + "/providers/Microsoft.KeyVault/vaults/all-secrets",
            params={"api-version": VERSIONS["Microsoft.KeyVault"]},
            headers=headers,
            json={
                "location": "eastus",
                "properties": {
                    "tenantId": str(tenant),
                    "sku": {"family": "A", "name": "standard"},
                    "accessPolicies": [
                        {
                            "tenantId": str(tenant),
                            "objectId": str(principal),
                            "permissions": {"secrets": ["all"]},
                        }
                    ],
                },
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["properties"]["accessPolicies"][0]["permissions"]["secrets"] == [
            "all"
        ]


async def test_relationship_database_rejects_cross_subscription_edges(database, settings):
    import pytest
    from sqlalchemy.exc import IntegrityError

    from mockazure.providers.arm.models import ResourceRelationship
    from tests.integration.test_arm_state import seed_arm

    async with graph_client(database, settings) as (client, tenant, app, user):
        first = await seed_arm(client, tenant)
        second = await seed_arm(client, tenant)
        with pytest.raises(IntegrityError):
            async with database.sessions.begin() as session:
                session.add(
                    ResourceRelationship(
                        tenant_id=tenant,
                        source_key=first[3]["id"].lower(),
                        target_key=second[3]["id"].lower(),
                        path="invalid-subscription-edge",
                    )
                )
                await session.flush()

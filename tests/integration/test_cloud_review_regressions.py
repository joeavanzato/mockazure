"""Whole-phase review regressions: literal scopes and recovery authority."""

import copy
from datetime import UTC, datetime

import pytest

from mockazure.cloud.models import CloudEvent
from mockazure.db.job_model import Job
from mockazure.evidence.models import EvidenceSource
from mockazure.jobs.dispatch import dispatch_job
from tests.integration.graph_support import graph_client
from tests.integration.test_cloud_resource_inventory import VERSIONS, create, prepare
from tests.integration.test_cloud_resource_lifecycle import ARGS, VM, claimed


@pytest.mark.parametrize("operation", ["read", "delete"])
async def test_scope_prefix_treats_underscore_literally(database, settings, operation):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        paths = []
        for suffix in ("_a", "xa"):
            rg = group + suffix
            assert (
                await client.put(
                    rg,
                    params={"api-version": "2021-04-01"},
                    headers=headers,
                    json={"location": "eastus"},
                )
            ).status_code in {200, 201}
            path = rg + "/providers/Microsoft.Compute/virtualMachines/vm"
            await create(client, path, VM, headers, ARGS["api-version"])
            await create(
                client,
                path + "/extensions/agent",
                {
                    "location": "eastus",
                    "properties": {
                        "publisher": "Microsoft.Azure.Extensions",
                        "type": "CustomScript",
                        "typeHandlerVersion": "2.1",
                        "settings": {},
                    },
                },
                headers,
                ARGS["api-version"],
            )
            paths.append(path)
        if operation == "read":
            response = await client.get(paths[0], params=ARGS, headers=headers)
            assert [row["id"] for row in response.json()["resources"]] == [
                paths[0] + "/extensions/agent"
            ]
            collection = await client.get(paths[0] + "/extensions", params=ARGS, headers=headers)
            assert [row["id"] for row in collection.json()["value"]] == [
                paths[0] + "/extensions/agent"
            ]
        else:
            assert (
                await client.post(paths[1] + "/deallocate", params=ARGS, headers=headers)
            ).status_code == 202
            now = datetime.now(UTC)
            other = await claimed(database, tenant, now)
            assert (await client.delete(paths[0], params=ARGS, headers=headers)).status_code == 202
            now = datetime.now(UTC)
            deleting = await claimed(database, tenant, now)
            assert (await dispatch_job(database, deleting, now)).state == "succeeded"
            assert (
                await client.get(paths[1] + "/extensions/agent", params=ARGS, headers=headers)
            ).status_code == 200
            assert (await dispatch_job(database, other, datetime.now(UTC))).state == "succeeded"


async def test_combined_restore_retires_group_deletion_before_parents(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        path = group + "/providers/Microsoft.Compute/virtualMachines/restored"
        await create(client, path, VM, headers, ARGS["api-version"])
        state = f"/_mock/tenants/{tenant}/cloud/state"
        snapshot = (await client.get(state)).json()
        assert (
            await client.delete(group, params={"api-version": "2021-04-01"}, headers=headers)
        ).status_code == 202
        now = datetime.now(UTC)
        old = await claimed(database, tenant, now, kind="arm.group.delete")
        invalid = copy.deepcopy(snapshot)
        invalid["resources"]["resources"][0]["configuration"]["properties"]["hardwareProfile"][
            "vmSize"
        ] = "unsupported"
        assert (await client.put(state, json=invalid)).status_code == 422
        async with database.sessions() as session:
            assert (await session.get(Job, old.id)).state == "running"
        restored = await client.put(state, json=snapshot)
        assert restored.status_code == 200, restored.text
        assert (await dispatch_job(database, old, datetime.now(UTC))).state == "stale"
        assert (await client.get(path, params=ARGS, headers=headers)).status_code == 200


async def test_subnet_shrink_preserves_existing_static_nics(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        version = VERSIONS["Microsoft.Network"]
        vnet = group + "/providers/Microsoft.Network/virtualNetworks/static"
        await create(
            client,
            vnet,
            {
                "location": "eastus",
                "properties": {"addressSpace": {"addressPrefixes": ["10.0.0.0/16"]}},
            },
            headers,
            version,
        )
        subnet = vnet + "/subnets/static"
        await create(
            client, subnet, {"properties": {"addressPrefix": "10.0.0.0/24"}}, headers, version
        )
        nic = group + "/providers/Microsoft.Network/networkInterfaces/static"
        await create(
            client,
            nic,
            {
                "location": "eastus",
                "properties": {
                    "ipConfigurations": [
                        {
                            "name": "primary",
                            "properties": {
                                "subnet": {"id": subnet},
                                "privateIPAllocationMethod": "Static",
                                "privateIPAddress": "10.0.0.200",
                            },
                        }
                    ]
                },
            },
            headers,
            version,
        )
        response = await client.put(
            subnet,
            params={"api-version": version},
            headers=headers,
            json={"properties": {"addressPrefix": "10.0.0.0/25"}},
        )
        assert response.status_code == 400, response.text
        assert (await client.get(subnet, params={"api-version": version}, headers=headers)).json()[
            "properties"
        ]["addressPrefix"] == "10.0.0.0/24"


@pytest.mark.parametrize("reset", [False, True])
async def test_standalone_resource_restore_retires_claims_without_new_facts(
    database, settings, reset
):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        path = group + "/providers/Microsoft.Compute/virtualMachines/restore-claim"
        await create(client, path, VM, headers, ARGS["api-version"])
        state = f"/_mock/tenants/{tenant}/cloud/resourceState"
        snapshot = (await client.get(state)).json()
        assert (
            await client.post(path + "/deallocate", params=ARGS, headers=headers)
        ).status_code == 202
        now = datetime.now(UTC)
        old = await claimed(database, tenant, now)
        restored = await client.put(state, json={"resources": []} if reset else snapshot)
        assert restored.status_code == 200, restored.text
        assert (await dispatch_job(database, old, datetime.now(UTC))).state == "stale"
        async with database.sessions() as session:
            assert (await session.get(Job, old.id)).state == "failed"
            assert await session.get(EvidenceSource, (tenant, "arm", old.id)) is None
            assert await session.get(CloudEvent, (tenant, old.id)) is None

from uuid import uuid4

from mockazure.population.catalog import get_adapter
from mockazure.population.types import Mutation, ResourceQuery
from tests.integration.intune_support import INTUNE_PLAN, INTUNE_SKU
from tests.integration.test_population_adapters import context
from tests.integration.test_population_workloads import TIMES
from tools.intune_fixtures import payload


async def test_explicit_licensing_enables_typed_intune_population(database, settings):
    ctx = await context(database, settings)
    mutation = Mutation(
        operation="provision",
        logical_key="intune",
        payload={
            "id": INTUNE_SKU,
            "sku_part_number": "INTUNE_A",
            "enabled": 20,
            "service_plans": [
                {
                    "servicePlanId": INTUNE_PLAN,
                    "servicePlanName": "INTUNE_A",
                    "provisioningStatus": "Success",
                }
            ],
        },
    )
    adapter = get_adapter("identity.skus")
    assert (await adapter.apply(ctx, [mutation])).effects["created"] == 1
    assert (await adapter.apply(ctx, [mutation])).effects["unchanged"] == 1
    device = Mutation(
        operation="import",
        logical_key="device",
        parent="deviceManagement",
        payload=payload("managedDevice", str(uuid4()), deviceName="LAB-PC"),
    )
    await get_adapter("intune.managedDevice").apply(ctx, [device])
    assert len((await get_adapter("intune.managedDevice").browse(ctx, ResourceQuery())).items) == 1


async def test_workload_relationship_append_is_replayable_and_preserves_other_edges(
    database, settings
):
    ctx = await context(database, settings)
    site = "lab.test," + str(uuid4()) + "," + str(uuid4())
    await get_adapter("sharepoint.site").apply(
        ctx,
        [
            Mutation(
                operation="import",
                logical_key="site",
                parent=site,
                payload={**TIMES, "id": site, "displayName": "Site"},
            )
        ],
    )
    adapter = get_adapter("sharepoint.relationships")
    for index in range(2):
        drive = str(uuid4())
        await get_adapter("sharepoint.drive").apply(
            ctx,
            [
                Mutation(
                    operation="import",
                    logical_key="drive",
                    parent=site,
                    payload={**TIMES, "id": drive, "name": "Docs"},
                )
            ],
        )
        mutation = Mutation(
            operation="link",
            logical_key=f"drive-{index}",
            parent=site,
            payload={
                "source": {"kind": "site", "id": site},
                "relation": "drives",
                "target": {"kind": "drive", "id": drive},
            },
        )
        assert (await adapter.apply(ctx, [mutation])).effects["created"] == 1
        assert (await adapter.apply(ctx, [mutation])).effects["unchanged"] == 1
    assert len((await adapter.browse(ctx, ResourceQuery(parent=site))).items) == 2

from uuid import uuid4

import pytest

from mockazure.admin.errors import AdminError
from mockazure.population.adapters import relationships
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


async def test_relationship_batch_validates_scope_once_and_rolls_back_invalid_edge(
    database, settings, monkeypatch
):
    ctx = await context(database, settings)
    scope = "lab.test," + str(uuid4()) + "," + str(uuid4())
    await get_adapter("sharepoint.site").apply(
        ctx,
        [
            Mutation(
                operation="import",
                logical_key="site",
                parent=scope,
                payload={**TIMES, "id": scope, "displayName": "Batch site"},
            )
        ],
    )
    drives = [str(uuid4()) for _ in range(102)]
    await get_adapter("sharepoint.drive").apply(
        ctx,
        [
            Mutation(
                operation="import",
                logical_key=f"drive-{i}",
                parent=scope,
                payload={**TIMES, "id": drive, "name": f"Docs {i}"},
            )
            for i, drive in enumerate(drives)
        ],
    )

    def link(drive):
        return Mutation(
            operation="link",
            logical_key=drive,
            parent=scope,
            payload={
                "source": {"kind": "site", "id": scope},
                "relation": "drives",
                "target": {"kind": "drive", "id": drive},
            },
        )

    adapter = get_adapter("sharepoint.relationships")
    await adapter.apply(ctx, [link(drives[0])])
    calls = []
    original = relationships.bounded_import_workload

    async def tracked(session, tenant, workload, batch):
        calls.append((batch.scope, len(batch.edges)))
        return await original(session, tenant, workload, batch)

    monkeypatch.setattr(relationships, "bounded_import_workload", tracked)
    batch = [link(drive) for drive in drives[1:101]]
    result = await adapter.apply(ctx, batch)
    assert result.effects["created"] == 100
    assert calls == [(scope, 100)]
    assert (await adapter.verify(ctx, result)).status == "succeeded"
    assert (await adapter.apply(ctx, batch)).effects["unchanged"] == 100
    with pytest.raises(AdminError, match="Edge key and public ID"):
        await adapter.apply(ctx, [link(drives[-1]), link(str(uuid4()))])
    rows = await adapter.browse(ctx, ResourceQuery(parent=scope, limit=200))
    assert len(rows.items) == 101

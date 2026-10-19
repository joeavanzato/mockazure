from datetime import UTC, datetime
from uuid import uuid4

from mockazure.population.catalog import get_adapter
from mockazure.population.types import Mutation, ResourceQuery
from tests.integration.test_population_adapters import context


async def test_arm_inventory_profile_subscription_group_resource_roundtrip(database, settings):
    ctx = await context(database, settings)
    sub = str(uuid4())
    subscription = Mutation(
        operation="import", logical_key="subscription", payload={"id": sub, "displayName": "Lab"}
    )
    await get_adapter("arm.subscriptions").apply(ctx, [subscription])
    group = f"/subscriptions/{sub}/resourceGroups/lab"
    await get_adapter("arm.resourceGroups").apply(
        ctx,
        [
            Mutation(
                operation="import",
                logical_key="group",
                payload={"id": group, "location": "eastus", "tags": {"dataset": "lab"}},
            )
        ],
    )
    resource = group + "/providers/Microsoft.Compute/virtualMachines/vm1"
    vm = Mutation(
        operation="import",
        logical_key="vm",
        payload={
            "id": resource,
            "name": "vm1",
            "type": "Microsoft.Compute/virtualMachines",
            "location": "eastus",
        },
    )
    adapter = get_adapter("arm.inventory")
    await adapter.apply(ctx, [vm])
    assert (await adapter.apply(ctx, [vm])).effects["unchanged"] == 1
    page = await adapter.browse(ctx, ResourceQuery())
    assert page.items[0].properties["name"] == "vm1"


async def test_activity_feed_import_replays_and_browse_does_not_change_subscription(
    database, settings
):
    ctx = await context(database, settings)
    record = {
        "Id": str(uuid4()),
        "OrganizationId": str(ctx.tenant_id),
        "CreationTime": datetime.now(UTC).isoformat(),
        "Operation": "FileAccessed",
        "UserKey": "user",
        "UserId": "user@lab.test",
        "Workload": "SharePoint",
        "RecordType": 6,
        "UserType": 0,
    }
    adapter = get_adapter("activity.records")
    mutation = Mutation(operation="import", logical_key="activity", payload=record)
    result = await adapter.apply(ctx, [mutation])
    assert result.effects["created"] == 1
    assert (await adapter.apply(ctx, [mutation])).effects["unchanged"] == 1
    page = await adapter.browse(ctx, ResourceQuery(identifier=record["Id"]))
    assert page.items[0].properties == record


async def test_subscription_receipt_survives_child_inventory_changes(database, settings):
    from uuid import uuid4

    from mockazure.population.catalog import get_adapter
    from mockazure.population.types import Mutation
    from mockazure.providers.arm.models import Subscription
    from tests.integration.test_population_adapters import context

    ctx = await context(database, settings)
    identifier = uuid4()
    adapter = get_adapter("arm.subscriptions")
    result = await adapter.apply(
        ctx,
        [
            Mutation(
                operation="import",
                logical_key="subscription",
                payload={"id": str(identifier), "displayName": "Lab"},
            )
        ],
    )
    await get_adapter("arm.resourceGroups").apply(
        ctx,
        [
            Mutation(
                operation="import",
                logical_key="group",
                payload={
                    "id": f"/subscriptions/{identifier}/resourceGroups/lab",
                    "location": "eastus",
                },
            )
        ],
    )
    assert (await adapter.verify(ctx, result)).status == "succeeded"
    async with database.sessions.begin() as session:
        row = await session.get(Subscription, identifier)
        row.display_name = "Changed"
    assert (await adapter.verify(ctx, result)).status == "failed"

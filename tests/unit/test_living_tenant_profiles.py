from datetime import UTC, datetime

from mockazure.population.catalog import get_adapter
from mockazure.population.generators import generate, inventory
from mockazure.profiles.builtin import builtin_bundle
from mockazure.profiles.resolver import resolve_template
from tests.unit.test_business_relationships import PARAMETERS, values


def test_all_templates_resolve_without_copying_base_layers():
    bundle = builtin_bundle()
    for name, users, days in (
        ("living-small-business", 20, 7),
        ("living-department", 250, 14),
        ("living-enterprise", 2500, 30),
    ):
        template = bundle.documents[name]
        assert not template.steps
        assert {
            "business-mail-calendar",
            "business-sharepoint-teams",
            "business-device-estate",
            "business-azure-operations",
        } <= set(template.dataProfiles)
        resolved = resolve_template(bundle, name, {}, datetime(2026, 10, 20, tzinfo=UTC))
        assert resolved.parameters["shared.users"] == users
        assert resolved.parameters["shared.days"] == days
        for step in resolved.steps.values():
            operation = next(
                op
                for op in get_adapter(step.family).describe().operations
                if op.key == step.operation
            )
            assert operation.mode == step.mode
            assert inventory()[step.parameters["generator"]]["family"] == step.family


def test_directory_intune_device_link_is_explicit():
    device = values("directory-devices", 2)[0]
    managed = values("managed-devices", 2)[0]
    assert device.payload["payload"]["deviceId"] == managed.payload["payload"]["azureADDeviceId"]
    assert device.payload["owner_id"] == managed.payload["payload"]["userId"]
    assert device.payload["payload"]["id"] != managed.payload["payload"]["azureADDeviceId"]
    for name in (
        "directory-devices",
        "managed-devices",
        "detected-apps",
        "device-configurations",
        "compliance-policies",
        "compliance-statuses",
        "configuration-statuses",
        "software-links",
        "business-applications",
    ):
        mutations = values(name, 2)
        adapter = get_adapter(inventory()["business." + name]["family"])
        for mutation in mutations:
            adapter.validate(mutation)
        assert mutations == values(name, 2, limit=1) + values(name, 2, offset=1, limit=1)


def test_resource_owners_and_diagnostic_destinations_resolve():
    refs = {
        "domain": "contoso.test",
        "group": "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/business",
    }
    rows = generate(
        "business.azure-inventory", {**PARAMETERS, "count": 8}, refs, offset=0, limit=20
    )
    identifiers = {row.payload["id"] for row in rows}
    users = {row.payload["id"] for row in values("users", 4)}
    assert all(row.payload["tags"]["ownerObjectId"] in users for row in rows)
    for row in rows:
        assert row.payload["id"].startswith(refs["group"] + "/providers/")
        if row.payload["type"] == "Microsoft.Compute/virtualMachines":
            assert (
                row.payload["properties"]["networkProfile"]["networkInterfaces"][0]["id"]
                in identifiers
            )

from uuid import UUID

import pytest

SUB = "12345678-1234-1234-1234-123456789abc"


def test_scope_case_and_segment_inheritance():
    from mockazure.providers.arm.scopes import parse_scope

    base = "/subscriptions/" + SUB
    group = parse_scope(base.upper() + "/RESOURCEGROUPS/Investigation")
    same = parse_scope(base + "/resourceGroups/investigation")
    resource = parse_scope(
        base + "/resourceGroups/Investigation/providers/Microsoft.Network/"
        "virtualNetworks/vnet/subnets/private"
    )
    assert group.key == same.key
    assert group.subscription_id == UUID(SUB)
    assert group.contains(resource)
    assert parse_scope(base).contains(resource)
    assert not same.contains(parse_scope(base + "/resourceGroups/investigations"))
    assert not resource.contains(group)
    assert group.key in resource.parent_keys
    assert resource.display_id.endswith("/subnets/private")


@pytest.mark.parametrize(
    "suffix",
    [
        "/resourceGroups/",
        "/resourceGroups/../",
        "/resourceGroups/a%2fb",
        "/resourceGroups/a%252fb",
        "/resourceGroups/a\\b",
        "/resourceGroups/a?secret=x",
        "/resourceGroups/a#fragment",
        "/resourceGroups//a",
        "/resourceGroups/./providers/x/y/z",
        "/resourceGroups/a/providers/Microsoft.Network/virtualNetworks",
        "/resourceGroups/a/providers/Microsoft.Network/virtualNetworks/vnet/subnets",
    ],
)
def test_ambiguous_scopes_rejected(suffix):
    from mockazure.providers.arm.scopes import parse_scope

    with pytest.raises(ValueError):
        parse_scope("/subscriptions/" + SUB + suffix)


def test_invalid_subscription_and_relative_scope_rejected():
    from mockazure.providers.arm.scopes import parse_scope

    for value in ("/subscriptions/nope", "subscriptions/" + SUB, "/", "/tenants/" + SUB):
        with pytest.raises(ValueError):
            parse_scope(value)


def test_official_schema_validation_and_versions():
    from mockazure.providers.arm.contracts import operation_contract, validate_arm_payload

    group = {
        "id": "/subscriptions/" + SUB + "/resourceGroups/lab",
        "name": "lab",
        "type": "Microsoft.Resources/resourceGroups",
        "location": "eastus",
        "tags": {"Purpose": "DFIR"},
        "properties": {"provisioningState": "Succeeded"},
    }
    assert validate_arm_payload("ResourceGroup", group) == group
    for bad in (
        group | {"invented": True},
        group | {"location": 1},
        group | {"tags": {"Purpose": 42}},
    ):
        with pytest.raises(ValueError):
            validate_arm_payload("ResourceGroup", bad)
    event = {
        "eventDataId": SUB,
        "eventTimestamp": "2026-10-17T09:00:00Z",
        "resourceId": group["id"],
        "operationName": {"value": "write", "localizedValue": "Write"},
        "status": {"value": "Succeeded"},
    }
    assert validate_arm_payload("EventData", event) == event
    with pytest.raises(ValueError):
        validate_arm_payload("EventData", event | {"eventTimestamp": "yesterday"})
    assert operation_contract("subscriptions.list", "2020-01-01")["method"] == "GET"
    assert operation_contract("activity.list", "2015-04-01")["schema"] == "EventDataCollection"
    for operation, version in (
        ("activity.list", "2021-04-01"),
        ("resources.create", "2021-04-01"),
        ("groups.get", "2099-01-01"),
    ):
        with pytest.raises(ValueError):
            operation_contract(operation, version)


def test_reference_bytes_and_builtin_permissions():
    from mockazure.providers.arm.contracts import builtin_roles, verify_references

    assert verify_references() >= 10
    roles = builtin_roles()
    assert roles["Reader"]["id"] == "acdd72a7-3385-48ef-bd42-f606fba81ae7"
    assert roles["Reader"]["actions"] == ["*/read"]
    assert roles["Owner"]["actions"] == ["*"]
    assert "Microsoft.Authorization/*/Write" in roles["Contributor"]["notActions"]
    assert "Microsoft.Authorization/*" in roles["User Access Administrator"]["actions"]

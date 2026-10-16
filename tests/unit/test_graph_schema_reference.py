import pytest


def test_v1_schema_rejects_beta_shape_and_wrong_scalar_types():
    from mockazure.providers.graph.schema_reference import validate_payload

    valid = {
        "id": "event",
        "createdDateTime": "2026-10-16T12:00:00Z",
        "userId": "",
        "servicePrincipalId": None,
        "status": {"errorCode": 50126},
    }
    validate_payload("v1.0", "signIn", valid)
    for patch in [
        {"signInEventTypes": ["interactiveUser"]},
        {"createdDateTime": None},
        {"userId": None},
        {"status": {"errorCode": True}},
        {"riskState": "hidden"},
        {"status": {"client_secret": "forbidden"}},
    ]:
        with pytest.raises(ValueError):
            validate_payload("v1.0", "signIn", valid | patch)


def test_directory_schema_nested_shapes_and_required_fields():
    from mockazure.providers.graph.schema_reference import validate_payload

    valid = {
        "id": "audit",
        "activityDateTime": "2026-10-16T12:00:00Z",
        "category": "UserManagement",
        "activityDisplayName": "Update user",
        "initiatedBy": {"user": {"id": "actor", "userPrincipalName": "a@example.test"}},
        "targetResources": [
            {
                "id": "target",
                "modifiedProperties": [
                    {"displayName": "DisplayName", "oldValue": '"Before"', "newValue": '"After"'}
                ],
            }
        ],
    }
    validate_payload("v1.0", "directoryAudit", valid)
    for patch in [
        {"initiatedBy": None},
        {"targetResources": [{"modifiedProperties": [{"name": "DisplayName"}]}]},
        {"additionalDetails": [{"key": "key", "value": {"nested": "not a string"}}]},
    ]:
        with pytest.raises(ValueError):
            validate_payload("v1.0", "directoryAudit", valid | patch)

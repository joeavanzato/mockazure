import json

import pytest

from mockazure.evidence.datasets import validate_public_payload


def audit(change):
    return {
        "id": "9c91f788-600a-4c87-944b-53b98655eb4d",
        "activityDateTime": "2026-10-16T00:00:00Z",
        "activityDisplayName": "Update application",
        "category": "ApplicationManagement",
        "initiatedBy": {},
        "targetResources": [
            {
                "id": "a2505b65-67b7-4a4a-b670-2c1578a5a733",
                "type": "Application",
                "modifiedProperties": [change],
            }
        ],
    }


def test_security_audit_differences_reject_undeclared_fields_even_when_absent():
    with pytest.raises(ValueError):
        validate_public_payload(
            "v1.0",
            "directoryAudit",
            audit(
                {
                    "displayName": "invented",
                    "oldValue": "null",
                    "newValue": "null",
                }
            ),
        )


def test_security_audit_differences_reject_secret_material():
    with pytest.raises(ValueError):
        validate_public_payload(
            "v1.0",
            "directoryAudit",
            audit(
                {
                    "displayName": "passwordCredentials",
                    "oldValue": "null",
                    "newValue": json.dumps([{"secretText": "sensitive-value"}]),
                }
            ),
        )

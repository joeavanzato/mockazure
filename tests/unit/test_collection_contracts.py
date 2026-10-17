import pytest

from mockazure.providers.graph.schema_reference import validate_payload


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("alert", {"id": "legacy-1", "severity": "high", "status": "newAlert"}),
        (
            "security.alert",
            {
                "id": "alert-1",
                "status": "new",
                "severity": "high",
                "evidence": [
                    {
                        "@odata.type": "#microsoft.graph.security.userEvidence",
                        "userAccount": {"userPrincipalName": "analyst@contoso.test"},
                    }
                ],
            },
        ),
        ("security.incident", {"id": "123", "status": "active", "severity": "medium"}),
        ("secureScore", {"id": "score-1", "currentScore": 12.5, "maxScore": 30.0}),
        ("secureScoreControlProfile", {"id": "control-1", "maxScore": 10.0}),
    ],
)
def test_official_security_resource_closure(kind, payload):
    validate_payload("v1.0", kind, payload, partial=True)


def test_security_alert_rejects_wrong_enum_and_unknown_property():
    for payload in [{"status": "pretend"}, {"secretProperty": "x"}]:
        with pytest.raises(ValueError):
            validate_payload("v1.0", "security.alert", payload, partial=True)

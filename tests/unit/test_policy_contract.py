import copy

import pytest

from mockazure.providers.graph.errors import GraphError
from tests.policy_support import location_body, policy_body


def test_policy_schema_and_supported_matrix():
    from mockazure.security.policies import validate_policy

    for state in ("enabled", "disabled", "enabledForReportingButNotEnforced"):
        for control in ("block", "mfa", "compliantDevice"):
            assert validate_policy(policy_body(state, control))["state"] == state
    invalid = []
    for key, value in (
        ("state", "unknown"),
        ("displayName", ""),
        ("secret", "hidden"),
        ("sessionControls", {"signInFrequency": {"isEnabled": True}}),
        ("grantControls", {"operator": "XOR", "builtInControls": ["mfa"]}),
        ("grantControls", {"operator": "OR", "builtInControls": ["domainJoinedDevice"]}),
        ("grantControls", {"operator": "OR", "builtInControls": []}),
        ("grantControls", {"operator": "AND", "builtInControls": ["block", "mfa"]}),
        ("grantControls", None),
    ):
        invalid.append(policy_body() | {key: value})
    for name, value in (
        ("userRiskLevels", ["high"]),
        ("platforms", {"includePlatforms": ["unknownFutureValue"]}),
        ("clientAppTypes", ["exchangeActiveSync"]),
        ("devices", {"deviceFilter": {"mode": "include"}}),
        ("users", {"includeUsers": ["GuestsOrExternalUsers"]}),
        ("applications", {"includeApplications": ["Office365"]}),
        ("locations", {"includeLocations": ["not-a-uuid"]}),
    ):
        body = policy_body()
        body["conditions"][name] = value
        invalid.append(body)
    for body in invalid:
        with pytest.raises(GraphError):
            validate_policy(body)
    assert validate_policy({"state": "disabled"}, partial=True) == {"state": "disabled"}
    original = policy_body()
    validate_policy(original)
    assert original == policy_body()


@pytest.mark.parametrize("cidr", ["127.0.0.1", "not-a-network", "1.2.3.4/99", "::1/129"])
def test_named_location_rejects_invalid_ranges(cidr):
    from mockazure.security.policies import validate_named_location

    body = location_body()
    body["ipRanges"][0]["cidrAddress"] = cidr
    with pytest.raises(GraphError):
        validate_named_location(body)


def test_named_location_schema_and_address_family():
    from mockazure.security.policies import validate_named_location

    body = location_body()
    assert validate_named_location(body) == body
    bad = copy.deepcopy(body)
    bad["ipRanges"][0]["cidrAddress"] = "::1/128"
    with pytest.raises(GraphError):
        validate_named_location(bad)
    for bad in (
        body | {"isTrusted": "true"},
        body | {"ipRanges": []},
        body | {"@odata.type": "#microsoft.graph.countryNamedLocation"},
        body | {"secret": "hidden"},
    ):
        with pytest.raises(GraphError):
            validate_named_location(bad)


@pytest.mark.parametrize(
    "body",
    [
        {"displayName": None, "ipRanges": [], "@odata.type": "#microsoft.graph.ipNamedLocation"},
        {
            "displayName": "Null ranges",
            "ipRanges": None,
            "@odata.type": "#microsoft.graph.ipNamedLocation",
        },
        {"displayName": "Null type", "ipRanges": [], "@odata.type": None},
    ],
)
def test_null_location_values_are_rejected_as_client_errors(body):
    from mockazure.security.policies import validate_named_location

    with pytest.raises(GraphError):
        validate_named_location(body)

import pytest

from mockazure.providers.graph.errors import GraphError
from mockazure.security.policies import validate_policy
from tests.policy_support import policy_body


def test_equivalent_uuid_spellings_are_duplicate_targets():
    body = policy_body()
    body["conditions"]["users"]["includeUsers"] = [
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
    ]
    with pytest.raises(GraphError):
        validate_policy(body)


def test_policy_normalizes_identifiers_without_mutating_callers():
    body = policy_body()
    body["conditions"]["users"]["includeUsers"] = ["AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"]
    result = validate_policy(body)
    assert result["conditions"]["users"]["includeUsers"] == ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"]
    assert body["conditions"]["users"]["includeUsers"] == ["AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"]

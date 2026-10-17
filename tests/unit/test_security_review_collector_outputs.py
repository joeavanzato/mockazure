import json

import pytest

from tools.qualify_security_collectors import verify_denied_outputs


@pytest.mark.parametrize("payload", [
    {"AppObjectID": "known-app", "KeyID": "sensitive-credential-metadata"},
    [{"phoneNumber": "+1 5555550150"}],
    {"value": [{"displayName": "Sensitive policy", "state": "enabled"}]},
])
def test_denied_json_cannot_hide_disclosure_behind_empty_csv(tmp_path, payload):
    csv = tmp_path / "empty.csv"
    csv.write_text("")
    result = tmp_path / "output.json"
    result.write_text(json.dumps(payload))
    with pytest.raises(AssertionError):
        verify_denied_outputs("hawk_credentials", "application", [csv, result], "local@graph.test")


@pytest.mark.parametrize("payload", ["[]", "null", "", "  \n"])
def test_denied_empty_json_is_allowed(tmp_path, payload):
    result = tmp_path / "empty.json"
    result.write_text(payload)
    verify_denied_outputs("hawk_credentials", "application", [result], "local@graph.test")


def test_denied_malformed_json_fails_closed(tmp_path):
    result = tmp_path / "malformed.json"
    result.write_text('{"sensitive":')
    with pytest.raises((AssertionError, ValueError)):
        verify_denied_outputs("hawk_credentials", "delegated", [result], "local@graph.test")

import json

import pytest

from mockazure.profiles.loader import load_bundle


def test_json_and_yaml_have_same_typed_values():
    left = load_bundle({"base": b"kind: tenant\nname: base\nschemaVersion: 1\n"})
    right = load_bundle(
        {
            "base": json.dumps(
                {
                    "kind": "tenant",
                    "name": "base",
                    "schemaVersion": 1,
                }
            ).encode()
        }
    )
    assert left == right


@pytest.mark.parametrize(
    "data",
    [
        b"kind: tenant\nname: base\nname: other",
        b'{"kind":"tenant","name":"base","name":"other"}',
        b"kind: tenant\nname: &name base\nextends: *name",
        b"!!python/object/apply:os.system [echo bad]",
        b"kind: tenant\nname: base\nresources:\n  <<: {}",
        b"kind: tenant\nname: base\nunknown: value",
    ],
)
def test_unsafe_yaml_duplicate_keys_aliases_and_paths_fail(data):
    with pytest.raises(ValueError):
        load_bundle({"base": data})
    with pytest.raises(ValueError):
        load_bundle({"../base": b"kind: tenant\nname: base"})


def test_size_depth_and_document_limits():
    with pytest.raises(ValueError):
        load_bundle({"base": b" " * (1024 * 1024 + 1)})
    with pytest.raises(ValueError):
        load_bundle({str(i): b"kind: tenant\nname: base" for i in range(129)})
    with pytest.raises(ValueError):
        load_bundle({"base": ("[" * 40 + "0" + "]" * 40).encode()})


def test_errors_do_not_echo_input_secrets():
    secret = "private-lab-password-123"
    with pytest.raises(ValueError) as error:
        load_bundle({"base": json.dumps({"name": "base", "kind": secret}).encode()})
    assert secret not in str(error.value)


def test_timestamp_is_not_implicitly_coerced():
    bundle = load_bundle(
        {
            "base": b"""kind: tenant
name: base
parameters:
  referenceTime: 2026-10-01T00:00:00Z
"""
        }
    )
    assert bundle.documents["base"].parameters["referenceTime"] == "2026-10-01T00:00:00Z"

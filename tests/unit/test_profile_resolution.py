import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mockazure.profiles.identity import stable_id
from mockazure.profiles.loader import load_bundle
from mockazure.profiles.resolver import resolve_template

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def documents():
    return {
        "small": {
            "kind": "tenant",
            "name": "small",
            "parameterDefinitions": {
                "users": {"type": "integer", "default": 20, "shared": True, "minimum": 1},
            },
            "resources": {
                "analyst": {
                    "family": "directory.users",
                    "properties": {
                        "displayName": "Analyst",
                        "tags": ["old"],
                        "optional": "value",
                    },
                }
            },
        },
        "mid": {"kind": "tenant", "name": "mid", "extends": "small", "parameters": {"users": 500}},
        "activity": {
            "kind": "data",
            "name": "activity",
            "resources": {
                "analyst": {
                    "family": "directory.users",
                    "override": True,
                    "properties": {"tags": ["new"], "optional": None},
                }
            },
        },
        "lab": {
            "kind": "template",
            "name": "lab",
            "tenantProfile": "mid",
            "dataProfiles": ["activity"],
            "parameters": {"seed": 42},
        },
    }


def resolve(data=None, overrides=None):
    bundle = load_bundle({k: json.dumps(v).encode() for k, v in (data or documents()).items()})
    return resolve_template(bundle, "lab", overrides or {}, NOW)


def test_layering_list_replace_and_run_precedence():
    result = resolve(overrides={"users": 2500})
    assert result.parameters["shared.users"] == 2500
    assert result.resources["analyst"].properties["displayName"] == "Analyst"
    assert result.resources["analyst"].properties["tags"] == ["new"]
    assert result.seed == 42
    assert result.referenceTime == NOW


def test_conflicting_resource_requires_override():
    data = documents()
    data["activity"]["resources"]["analyst"].pop("override")
    with pytest.raises(ValueError, match="override"):
        resolve(data)


def test_null_is_not_delete():
    assert resolve().resources["analyst"].properties["optional"] is None


def test_missing_reference_and_inheritance_cycle_fail():
    data = documents()
    data["small"]["extends"] = "mid"
    with pytest.raises(ValueError, match="cycle"):
        resolve(data)
    data = documents()
    data["activity"]["resources"]["analyst"]["properties"]["owner"] = {"$ref": "absent"}
    with pytest.raises(ValueError, match="reference"):
        resolve(data)


def test_dependency_does_not_install_profile():
    data = documents()
    data["activity"]["steps"] = {
        "event": {
            "mode": "fixture",
            "family": "evidence",
            "operation": "import",
            "dependsOn": ["missing-step"],
        }
    }
    with pytest.raises(ValueError, match="dependency"):
        resolve(data)


def test_parameter_namespace_and_origin():
    data = documents()
    data["activity"]["parameterDefinitions"] = {"count": {"type": "integer", "default": 3}}
    data["activity"]["resources"]["analyst"]["properties"]["count"] = {"$param": "activity.count"}
    result = resolve(data, {"activity.count": 9})
    assert result.resources["analyst"].properties["count"] == 9
    assert result.origins["/parameters/activity.count"] == "run"
    assert result.origins["/resources/analyst/properties/displayName"] == "small"


def test_equivalent_serialization_has_same_digest_and_namespace_ids():
    data = documents()
    other = load_bundle({k: json.dumps(v, indent=2).encode() for k, v in data.items()})
    assert resolve().digest == resolve_template(other, "lab", {}, NOW).digest
    tenant = uuid4()
    assert stable_id(tenant, "lab", "analyst", 42) == stable_id(tenant, "lab", "analyst", 42)
    assert stable_id(tenant, "lab", "analyst", 42) != stable_id(uuid4(), "lab", "analyst", 42)


def test_invalid_parameter_unknown_override_and_naive_time_fail():
    for overrides in ({"users": 0}, {"users": True}, {"unknown": 1}):
        with pytest.raises(ValueError):
            resolve(overrides=overrides)
    bundle = load_bundle({k: json.dumps(v).encode() for k, v in documents().items()})
    with pytest.raises(ValueError):
        resolve_template(bundle, "lab", {}, NOW.replace(tzinfo=None))


def test_template_inheritance_and_dependency_stable_order():
    data = documents()
    data["parent"] = {
        "kind": "template",
        "name": "parent",
        "tenantProfile": "mid",
        "dataProfiles": ["activity"],
        "parameters": {"seed": 4},
    }
    data["lab"] = {
        "kind": "template",
        "name": "lab",
        "extends": "parent",
        "parameters": {"seed": 42},
    }
    step = {"mode": "fixture", "family": "evidence", "operation": "import"}
    data["activity"]["steps"] = {"second": {**step, "dependsOn": ["first"]}, "first": step}
    assert list(resolve(data).steps) == ["first", "second"]


def test_metadata_and_reference_ambiguity_fail():
    data = documents()
    data["small"]["dataProfiles"] = ["activity"]
    with pytest.raises(ValueError):
        resolve(data)


def test_unqualified_parameter_cannot_change_unrelated_profile():
    data = documents()
    data["small"]["parameterDefinitions"]["count"] = {"type": "integer", "default": 2}
    data["activity"]["parameters"] = {"count": 900}
    with pytest.raises(ValueError, match="parameter"):
        resolve(data)


def test_child_can_override_its_inherited_private_parameter():
    data = documents()
    data["small"]["parameterDefinitions"]["count"] = {"type": "integer", "default": 2}
    data["mid"]["parameters"]["count"] = 3
    assert resolve(data).parameters["small.count"] == 3


def test_inherited_parameter_definition_retains_unsupplied_constraints():
    data = documents()
    data["small"]["parameterDefinitions"]["users"]["maximum"] = 1000
    data["mid"]["parameterDefinitions"] = {
        "users": {"type": "integer", "shared": True, "default": 500}
    }
    with pytest.raises(ValueError, match="bounds"):
        resolve(data, {"users": 2500})
    data = documents()
    data["activity"]["resources"]["analyst"]["properties"]["value"] = {
        "$param": "shared.seed",
        "extra": "ignored?",
    }
    with pytest.raises(ValueError):
        resolve(data)


def test_long_valid_dependency_chain_uses_bounded_iterative_ordering():
    from mockazure.profiles.models import StepDefinition
    from mockazure.profiles.resolver import order_steps

    steps = {
        f"s{i}": StepDefinition(
            mode="fixture",
            family="directory.users",
            operation="import",
            parameters={},
            dependsOn=[f"s{i + 1}"] if i < 1023 else [],
        )
        for i in range(1024)
    }
    assert list(order_steps(steps))[0] == "s1023"

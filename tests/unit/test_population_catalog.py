import pytest

from mockazure.population.catalog import get_adapter, list_families
from mockazure.population.types import Mutation, ResourceQuery


def test_catalog_operations_are_explicit_and_versioned():
    families = list_families()
    assert len({item.key for item in families}) == len(families)
    users = get_adapter("directory.users").describe()
    assert users.api_path == "/v1.0/users"
    assert users.operations[0].mode == "fixture"
    assert users.contract_refs
    with pytest.raises(ValueError):
        get_adapter("unknown")


def test_unknown_fields_and_unsupported_operation_fail():
    adapter = get_adapter("directory.users")
    with pytest.raises(ValueError):
        adapter.validate(Mutation(operation="execute-code", logical_key="x", payload={}))
    with pytest.raises(ValueError):
        adapter.validate(Mutation(operation="import", logical_key="x", payload={"invented": 1}))
    with pytest.raises(ValueError):
        ResourceQuery(limit=501)


def test_security_collection_uses_official_graph_contract():
    adapter = get_adapter("security.alerts_v2")
    adapter.validate(
        Mutation(
            operation="import",
            logical_key="alert",
            payload={"id": "alert-1", "title": "Synthetic", "severity": "high"},
        )
    )
    with pytest.raises(ValueError):
        adapter.validate(
            Mutation(
                operation="import",
                logical_key="alert",
                payload={"id": "alert-1", "inventedField": 1},
            )
        )

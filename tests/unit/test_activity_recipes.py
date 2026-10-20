from copy import deepcopy

import pytest

from mockazure.population.activity import VERSION, action_at, validate_recipe
from mockazure.population.catalog import get_adapter
from mockazure.profiles.builtin import builtin_bundle
from mockazure.profiles.resolver import resolve_template
from tests.unit.test_population_generators import PARAMETERS, REFS


def snapshot(step="calendar"):
    from datetime import UTC, datetime

    profile = resolve_template(
        builtin_bundle(), "collection-lab", {}, datetime(2026, 10, 7, tzinfo=UTC)
    )
    return {
        "tenant": PARAMETERS["tenant"],
        "activity_version": VERSION,
        "request": {
            "dataset_namespace": "activity",
            "activity": {
                "recipe_step": step,
                "action_count": 100000,
                "pacing_ms": 0,
            },
        },
        "resolved": profile.model_dump(mode="json"),
    }


@pytest.mark.parametrize(
    "step,family",
    [
        ("calendar", "mail.events"),
        ("blob", "storage.blobs"),
        ("file", "storage.files"),
        ("entity", "storage.entities"),
        ("queue-message", "storage.messages"),
        ("secret", "keyvault.secrets"),
    ],
)
def test_random_access_recipes_are_bounded_unique_and_validate(step, family):
    value = snapshot(step)
    before = deepcopy(value)
    refs = REFS | {
        "client": "lab-client",
        "account": REFS["group"] + "/providers/Microsoft.Storage/storageAccounts/labaccount",
        "vault": REFS["group"] + "/providers/Microsoft.KeyVault/vaults/lab-vault",
        "content": "66666666-6666-6666-6666-666666666666",
        "table": "Evidence",
    }
    validate_recipe(value)
    first, last = action_at(value, 0, refs), action_at(value, 99999, refs)
    assert first != last and first == action_at(value, 0, refs)
    get_adapter(family).validate(first)
    get_adapter(family).validate(last)
    assert value == before
    assert len(value["resolved"]["steps"]) < 100


def test_fixture_or_setup_activity_is_rejected():
    for key in ("users", "account", "missing"):
        with pytest.raises(ValueError):
            validate_recipe(snapshot(key))

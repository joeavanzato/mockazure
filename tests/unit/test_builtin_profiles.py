from datetime import UTC, datetime

from mockazure.population.catalog import get_adapter, list_families
from mockazure.population.generators import generate, inventory
from mockazure.population.generators.recipes import PROVIDERS, RECIPE_FAMILIES, ROLES
from mockazure.profiles.builtin import builtin_bundle, catalog_inventory
from mockazure.profiles.resolver import resolve_template
from tests.unit.test_population_generators import PARAMETERS, REFS


def test_catalog_inventory_has_no_silent_missing_family():
    expected = catalog_inventory()
    assert set(expected["families"]) == {f.key for f in list_families()}
    assert set(expected["profiles"]) == set(builtin_bundle().documents)
    assert set(expected["generators"]) == set(inventory())


def test_all_stacked_templates_resolve_and_declare_supported_operations():
    bundle = builtin_bundle()
    assert len(bundle.documents) >= 18
    for name in ("developer-lab", "collection-lab", "investigation-lab", "volume-lab"):
        resolved = resolve_template(bundle, name, {}, datetime(2026, 10, 7, tzinfo=UTC))
        for step in resolved.steps.values():
            descriptor = get_adapter(step.family).describe()
            operation = next(o for o in descriptor.operations if o.key == step.operation)
            assert step.mode == operation.mode
            assert inventory()[step.parameters["generator"]]["family"] == step.family
    assert (
        resolve_template(bundle, "developer-lab", {}, datetime.now(UTC)).parameters["shared.users"]
        == 20
    )


def test_all_setup_recipes_validate_against_the_actual_catalog():
    refs = REFS | {
        "client": "lab-client",
        "subscription": "11111111-1111-1111-1111-111111111111",
        "account": REFS["group"] + "/providers/Microsoft.Storage/storageAccounts/labaccount",
        "vault": REFS["group"] + "/providers/Microsoft.KeyVault/vaults/lab-vault",
        "content": "66666666-6666-6666-6666-666666666666",
        "table": "Evidence",
    }
    for name, (family, _, _) in RECIPE_FAMILIES.items():
        count = (
            len(ROLES)
            if name == "roles"
            else len(PROVIDERS)
            if name == "providers"
            else 3
            if name == "mail-folders"
            else 1
        )
        mutations = generate(
            "recipe." + name, PARAMETERS | {"count": count}, refs, offset=0, limit=100
        )
        assert len(mutations) == count
        for mutation in mutations:
            get_adapter(family).validate(mutation)

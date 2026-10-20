from uuid import uuid4

from mockazure.population.catalog import get_adapter
from mockazure.population.generators import generate, inventory


def parameters():
    return {
        "tenant": str(uuid4()),
        "namespace": "disposable-lifecycle",
        "referenceTime": "2026-10-07T00:00:00Z",
        "count": 1,
    }


def test_lifecycle_recipes_bind_returned_subject_and_have_no_credentials():
    params = parameters()
    refs = {
        "domain": "local.test",
        "lifecycle-user": str(uuid4()),
        "lifecycle-client": "named-client",
    }
    for kind in ("joiner", "mover", "leaver", "delete", "restore", "eligibility"):
        name = "lifecycle." + kind
        assert name in inventory()
        mutation = generate(name, params, refs, offset=0, limit=1)[0]
        adapter = get_adapter(inventory()[name]["family"])
        adapter.validate(mutation)
        if kind != "joiner":
            assert refs["lifecycle-user"] in str(mutation.payload)
        assert "password" not in str(mutation.payload).lower()
        assert "secret" not in str(mutation.payload).lower()
        assert mutation.identity_ref == refs["lifecycle-client"]


def test_destructive_lifecycle_actions_are_explicit_and_not_blindly_replayed():
    descriptor = get_adapter("identity.lifecycle").describe()
    operations = {op.key: op for op in descriptor.operations}
    for key in ("leaver", "delete", "restore"):
        assert operations[key].destructive
        assert operations[key].mode == "service"
        assert operations[key].replay_policy == "manual"


def test_lifecycle_inspector_keeps_deleted_and_pim_separate():
    for family in ("identity.deletedObjects", "identity.pimEligibility", "identity.pimAssignments"):
        descriptor = get_adapter(family).describe()
        assert descriptor.operations == []
        assert any("wall" in text for text in descriptor.limitations)

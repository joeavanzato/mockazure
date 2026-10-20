from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from mockazure.population.organization import OrganizationSpec, entity_at

TENANT = UUID("10000000-0000-0000-0000-000000000001")


def spec(**changes):
    return OrganizationSpec(
        **{
            "users": 2001,
            "departments": 6,
            "projects": 10,
            "locations": 3,
            "days": 7,
            "seed": 42,
            "reference_time": datetime(2026, 10, 20, tzinfo=UTC),
            **changes,
        }
    )


def test_seed_is_chunk_independent():
    settings = spec()
    whole = [entity_at(settings, TENANT, "business", "user", i) for i in range(settings.users)]
    for batch in (1, 100, 1000):
        chunked = [
            entity_at(settings, TENANT, "business", "user", i)
            for offset in range(0, settings.users, batch)
            for i in range(offset, min(offset + batch, settings.users))
        ]
        assert whole == chunked
    assert whole[0] != entity_at(spec(seed=43), TENANT, "business", "user", 0)
    assert (
        whole[0].properties["id"]
        != entity_at(settings, uuid4(), "business", "user", 0).properties["id"]
    )
    assert (
        whole[0].properties["id"]
        != entity_at(settings, TENANT, "another", "user", 0).properties["id"]
    )


def test_manager_graph_is_acyclic_and_relationships_resolve():
    settings = spec()
    users = {f"user:{i}": entity_at(settings, TENANT, "lab", "user", i) for i in range(2001)}
    for key, user in users.items():
        visited = {key}
        current = user
        while "manager" in current.relationships:
            parent = current.relationships["manager"]
            assert parent not in visited
            visited.add(parent)
            current = users[parent]
        for relation, target in user.relationships.items():
            if relation == "manager":
                continue
            kind, index = target.split(":")
            assert entity_at(settings, TENANT, "lab", kind, int(index)).key == target


@pytest.mark.parametrize(
    "kind,count",
    [
        ("department", 6),
        ("project", 10),
        ("location", 3),
        ("device", 2001),
        ("application", 10),
        ("subscription", 3),
        ("resourceGroup", 10),
    ],
)
def test_estate_entities_have_stable_resolvable_relationships(kind, count):
    settings = spec()
    for i in (0, count - 1):
        result = entity_at(settings, TENANT, "lab", kind, i)
        assert result.key == f"{kind}:{i}"
        assert result.kind == kind
        for target in result.relationships.values():
            target_kind, target_index = target.split(":")
            assert entity_at(settings, TENANT, "lab", target_kind, int(target_index)).key == target
    with pytest.raises(ValueError):
        entity_at(settings, TENANT, "lab", kind, count)


@pytest.mark.parametrize(
    "changes",
    [
        {"users": True},
        {"departments": 101},
        {"days": 0},
        {"reference_time": datetime(2026, 10, 20)},
        {"projects": 1001},
    ],
)
def test_organization_bounds_and_timezone(changes):
    with pytest.raises(ValidationError):
        spec(**changes)


@pytest.mark.parametrize("namespace,index", [("../other", 0), ("x", -1), ("x", True)])
def test_random_access_rejects_invalid_coordinates(namespace, index):
    with pytest.raises(ValueError):
        entity_at(spec(), TENANT, namespace, "user", index)


def test_small_organization_does_not_reference_missing_people():
    settings = spec(users=1)
    for kind in ("user", "project", "application", "device"):
        row = entity_at(settings, TENANT, "tiny", kind, 0)
        assert row.relationships.get("owner", "user:0") == "user:0"
    assert "manager" not in entity_at(settings, TENANT, "tiny", "user", 0).relationships

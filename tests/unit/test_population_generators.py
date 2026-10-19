from uuid import UUID

import pytest

from mockazure.population.catalog import get_adapter
from mockazure.population.generators import generate, inventory

PARAMETERS = {
    "tenant": "11111111-1111-1111-1111-111111111111",
    "namespace": "alpha",
    "seed": 7,
    "referenceTime": "2026-10-07T12:00:00Z",
    "days": 7,
    "users": 20,
    "count": 4,
}
REFS = {
    "domain": "lab.test",
    "primary-user": "22222222-2222-2222-2222-222222222222",
    "site": "lab.test,11111111-1111-1111-1111-111111111111,33333333-3333-3333-3333-333333333333",
    "team": "44444444-4444-4444-4444-444444444444",
    "drive": "drive-lab",
    "channel": "general",
    "group": "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/lab",
    "workspace": "55555555-5555-5555-5555-555555555555",
}


@pytest.mark.parametrize(
    "name",
    [
        "directory.users",
        "directory.groups",
        "evidence.signins",
        "evidence.suspicious-signins",
        "evidence.directory-audits",
        "security.alerts",
        "activity.records",
        "mail.messages",
        "workloads.sites",
        "workloads.drives",
        "workloads.teams",
        "workloads.channels",
        "workloads.devices",
        "cloud.inventory",
        "cloud.logs",
    ],
)
def test_generators_are_bounded_deterministic_and_contract_valid(name):
    batch = generate(name, PARAMETERS, REFS, offset=1, limit=2)
    assert len(batch) == 2
    assert batch == generate(name, PARAMETERS, REFS, offset=1, limit=2)
    assert batch == generate(name, PARAMETERS, REFS, offset=0, limit=4)[1:3]
    for mutation in batch:
        get_adapter(inventory()[name]["family"]).validate(mutation)
    other = generate(name, PARAMETERS | {"namespace": "beta"}, REFS, offset=1, limit=2)

    def identity(m):
        value = m.payload if "id" in m.payload or "Id" in m.payload else m.payload["payload"]
        return value.get("id", value.get("Id"))

    assert {identity(m) for m in batch}.isdisjoint(identity(m) for m in other)


def test_directory_and_signins_share_subjects_and_fixed_time():
    users = generate("directory.users", PARAMETERS | {"count": 20}, REFS, offset=0, limit=20)
    events = generate("evidence.signins", PARAMETERS | {"count": 20}, REFS, offset=0, limit=20)
    assert {r.payload["payload"]["userId"] for r in events} <= {r.payload["id"] for r in users}
    for user in users:
        UUID(user.payload["id"])
    assert generate("directory.users", PARAMETERS, REFS, offset=100, limit=2) == []
    with pytest.raises(ValueError):
        generate("directory.users", PARAMETERS, REFS, offset=0, limit=1001)


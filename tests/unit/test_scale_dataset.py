import copy

import pytest

from mockazure.directory.datasets import DirectoryDataset
from mockazure.evidence.datasets import EvidenceDataset


def test_scale_generators_are_deterministic_bounded_and_actual_schema_valid():
    from tools.scale_dataset import iter_directory_batches, iter_evidence_batches, user_id

    users = list(iter_directory_batches(42, 13, 5))
    assert users == list(iter_directory_batches(42, 13, 5))
    assert [len(batch["users"]) for batch in users] == [5, 5, 3]
    identifiers = {row["id"] for batch in users for row in batch["users"]}
    assert len(identifiers) == 13
    assert identifiers == {user_id(42, i) for i in range(13)}
    for batch in users:
        DirectoryDataset.model_validate(batch)
    records = list(iter_evidence_batches(42, 13, 31, 7))
    assert records == list(iter_evidence_batches(42, 13, 31, 7))
    assert sum(len(batch["records"]) for batch in records) == 31
    assert max(len(batch["records"]) for batch in records) == 7
    values = [row for batch in records for row in batch["records"]]
    assert len({row["payload"]["id"] for row in values}) == 31
    assert all(row["payload"]["userId"] in identifiers for row in values)
    for batch in records:
        EvidenceDataset.model_validate(batch)


@pytest.mark.parametrize(
    "seed,users,records,batch",
    [
        (True, 1, 1, 1),
        (42, 0, 1, 1),
        (42, 100001, 1, 1),
        (42, 1, 1000001, 1),
        (42, 1, 0, 1),
        (42, 1, 1, 0),
        (42, 1, 1, 1001),
    ],
)
def test_scale_generator_bounds(seed, users, records, batch):
    from tools.scale_dataset import iter_evidence_batches

    with pytest.raises(ValueError):
        next(iter_evidence_batches(seed, users, records, batch))


def test_exact_coverage_rejects_duplicate_missing_and_changed_records():
    from tools.scale_dataset import ExactCoverage, iter_evidence_batches

    rows = [r["payload"] for batch in iter_evidence_batches(42, 3, 10, 4) for r in batch["records"]]
    coverage = ExactCoverage("signIn", seed=42, users=3, records=10)
    for row in reversed(rows):
        coverage.add(row)
    assert coverage.finish()["count"] == 10
    with pytest.raises(AssertionError):
        coverage.add(rows[0])
    missing = ExactCoverage("signIn", seed=42, users=3, records=10)
    for row in rows[:-1]:
        missing.add(row)
    with pytest.raises(AssertionError):
        missing.finish()
    for field in ("id", "userId", "createdDateTime"):
        changed = copy.deepcopy(rows[0])
        changed[field] = rows[1][field]
        with pytest.raises((AssertionError, ValueError)):
            ExactCoverage("signIn", seed=42, users=3, records=10).add(changed)


def test_directory_coverage_requires_generated_ids_and_exact_user_count():
    from tools.scale_dataset import ExactCoverage, iter_directory_batches

    coverage = ExactCoverage("user", seed=42, users=13, records=0)
    for batch in iter_directory_batches(42, 13, 5):
        for row in batch["users"]:
            coverage.add(row)
    assert coverage.finish()["count"] == 13
    assert len(coverage.seen) == 2

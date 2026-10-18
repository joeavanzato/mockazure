import pytest

from tools.scale_dataset import collection_id, iter_collection_batches, streamed_block


def test_collection_batches_are_deterministic_bounded_and_complete():
    from uuid import UUID

    from mockazure.mail.content_models import MailContentImport
    from mockazure.workloads.imports import WorkloadImport

    user = str(UUID(int=1))
    for kind, model in (("message", MailContentImport), ("driveItem", WorkloadImport)):
        batches = list(iter_collection_batches(kind, seed=42, count=1031, user=user))
        assert batches == list(iter_collection_batches(kind, seed=42, count=1031, user=user))
        for batch in batches:
            model.model_validate(batch)
            if kind == "driveItem":
                from mockazure.workloads.sites import validate_site_drive_record

                for row in batch["records"]:
                    validate_site_drive_record(row["kind"], row["payload"])
        records = [
            r
            for b in batches
            for r in b.get("messages", b.get("records", []))
            if r.get("kind", kind) == kind
        ]
        assert {r["id"] for r in records} == {collection_id(kind, 42, i) for i in range(1031)}
    assert streamed_block(42, 0) == streamed_block(42, 0)
    assert streamed_block(42, 0) != streamed_block(42, 1)
    assert len(streamed_block(42, 0)) == 1024 * 1024


def test_collection_scale_rejects_unbounded_or_invalid_profiles():
    for kind, count in (("unsupported", 1), ("message", 0), ("driveItem", 1000001)):
        with pytest.raises(ValueError):
            list(
                iter_collection_batches(
                    kind, seed=42, count=count, user="00000000-0000-0000-0000-000000000001"
                )
            )

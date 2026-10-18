from uuid import uuid4

import pytest
from pydantic import ValidationError

from mockazure.workloads.imports import WorkloadImport
from mockazure.workloads.sites import validate_site_drive_record


def test_site_drive_types_are_pinned_and_fields_are_open_only_at_fieldvalueset():
    for kind, payload in (
        (
            "site",
            {
                "id": "local.test,site,web",
                "displayName": "Evidence",
                "webUrl": "https://local.test/sites/evidence",
            },
        ),
        (
            "driveItem",
            {"id": "opaque!item", "name": "evidence.txt", "file": {"mimeType": "text/plain"}},
        ),
        ("fieldValueSet", {"id": "fields-1", "Title": "Evidence", "ObservedCount": 17}),
    ):
        if kind != "fieldValueSet":
            payload.update(
                createdDateTime="2026-10-07T00:00:00Z", lastModifiedDateTime="2026-10-07T00:00:00Z"
            )
        validate_site_drive_record(kind, payload)
    with pytest.raises(ValueError):
        validate_site_drive_record("site", {"id": "s", "invented": True})
    with pytest.raises(ValueError):
        validate_site_drive_record(
            "fieldValueSet", {"id": "f", "@odata.nextLink": "https://external.invalid"}
        )


def test_workload_import_has_explicit_keys_and_combined_budget():
    valid = {
        "scope": "local.test,site,web",
        "expected_revision": 0,
        "records": [{"kind": "site", "id": "local.test,site,web", "payload": {}}],
        "edges": [],
        "tombstones": [],
    }
    assert WorkloadImport.model_validate(valid).scope == valid["scope"]
    with pytest.raises(ValidationError):
        WorkloadImport.model_validate({**valid, "records": valid["records"] * 2})
    with pytest.raises(ValidationError):
        WorkloadImport.model_validate(
            {
                **valid,
                "records": [
                    {"kind": "driveItem", "id": str(uuid4()), "payload": {}} for _ in range(1001)
                ],
            }
        )
    with pytest.raises(ValidationError):
        WorkloadImport.model_validate(
            {**valid, "tombstones": [{"kind": "site", "id": valid["scope"]}]}
        )

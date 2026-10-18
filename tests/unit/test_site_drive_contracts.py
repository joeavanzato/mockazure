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


@pytest.mark.parametrize("field", ["scope", "id"])
def test_workload_key_byte_limits_prevent_oversized_database_indexes(field):
    value = "\U0001f4c1" * 256
    batch = {
        "scope": value if field == "scope" else "site",
        "records": [{"kind": "site", "id": value if field == "id" else "site", "payload": {}}],
    }
    with pytest.raises(ValidationError):
        WorkloadImport.model_validate(batch)


def test_workload_page_links_encode_reserved_characters():
    from types import SimpleNamespace

    from mockazure.workloads.queries import link

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    advertised_endpoints=lambda host: SimpleNamespace(
                        graph="https://localhost:8443"
                    )
                )
            )
        ),
        headers={},
    )
    url = link(request, "/v1.0/drives/d/items/id%25#?/children", "abc")
    assert "id%2525%23%3F/children" in url


async def test_site_drive_capabilities_and_pinned_operation_sources(tmp_path):
    import json
    from pathlib import Path
    from types import SimpleNamespace

    from mockazure.admin.routes import capabilities
    from mockazure.config import Settings
    from mockazure.providers.graph.schema_reference import schema

    root = Path(__file__).resolve().parents[2]
    manifest = json.loads(
        (root / "integrations/contracts/site-drive.json").read_text(encoding="utf-8")
    )
    assert len(manifest["operations"]) == 22
    assert manifest["schemaRevision"] == schema("v1.0")["revision"]
    for row in manifest["operations"]:
        assert row["path"].startswith("/v1.0/")
        assert row["sourceUrl"].startswith("https://learn.microsoft.com/en-us/graph/api/")
        assert len(row["sourceSha256"]) == 64
        if row["responseType"]:
            assert row["responseType"] in schema("v1.0")["types"]
    settings = Settings(data_dir=tmp_path, database_url="postgresql+psycopg://localhost/mockazure")
    response = await capabilities(
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    )
    assert "graph.sites.selectedApplicationGrants" in response["provider_operations"]
    assert "graph.drives.rootDelta" in response["provider_operations"]
    assert "workloads.sharepoint.import" in response["admin_operations"]
    assert response["site_drive_profile"]["capability_seconds"] == 300
    assert response["site_drive_profile"]["beta"] is False

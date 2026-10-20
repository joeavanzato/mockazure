"""Live qualification gates; emitted evidence must be collected, never substituted."""

import json
from pathlib import Path

import pytest


def report():
    path = Path("work/living-evidence.json")
    assert path.is_file(), (
        "Run tools.verify_living_tenant --phase evidence against the owned instance"
    )
    return json.loads(path.read_text("utf-8"))


def test_action_to_collection_evidence_chain():
    from tools.verify_living_tenant import validate_report

    value = report()
    validate_report(value)
    assert value["missing_expected_ids"] == []
    assert {"authentication", "directory", "security", "mail", "workload", "arm", "service"} <= {
        row["source_kind"] for row in value["cases"]
    }
    assert {"blob", "file", "table", "queue", "keyvault"} == {
        row["service"] for row in value["cases"] if row["outcome"] == "failure"
    }
    assert {
        "graph-sdk",
        "exchange-module",
        "activity-feed",
        "arm-rest",
        "logs-rest",
        "blob-sdk",
    } <= {row["collector"] for row in value["cases"]}
    for row in value["cases"]:
        assert row["state"] == "passed" and row["source_ids"]
        assert row["expected_ids"] and set(row["expected_ids"]) <= set(row["observed_ids"])


def test_imported_history_is_not_native_delivery():
    value = report()
    assert value["import_check"]["stored"] is True
    assert value["import_check"]["native_sources"] == []
    assert value["import_check"]["native_deliveries"] == []


def test_evidence_clock_is_separate_from_access_expiry():
    value = report()
    assert value["clock_check"]["advanced_seconds"] > 3600
    assert (
        value["clock_check"]["access_token_expiry_before"]
        == (value["clock_check"]["access_token_expiry_after"])
    )
    assert value["clock_check"]["expired_token_status"] == 401


@pytest.mark.parametrize("field", ["cases", "import_check", "clock_check", "source_fingerprint"])
def test_qualification_rejects_incomplete_evidence(field):
    from tools.verify_living_tenant import validate_report

    value = report()
    del value[field]
    with pytest.raises(ValueError):
        validate_report(value)


def test_business_diagnostics_precede_every_data_plane_write():
    root = Path("src/mockazure/profiles/data/data")
    steps = json.loads((root / "business-azure-operations.json").read_text())["steps"]
    assert steps["diagnostics"]["parameters"]["count"] == 5
    assert set(steps["diagnostics"]["dependsOn"]) >= {"account", "vault", "workspace"}
    for name in ["blob", "file", "entity", "queue-message", "secret"]:
        assert "diagnostics" in steps[name]["dependsOn"]

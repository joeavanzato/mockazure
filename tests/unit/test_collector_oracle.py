from pathlib import Path
from unittest.mock import Mock


def test_target_or_expectations_include_existing_outside_window_state(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    from qualify_evidence_collectors import collector_expectations

    fixture = {
        "family": "directoryAudit",
        "payload": {"id": "fixture", "activityDisplayName": "fixture"},
    }
    older = {"family": "directoryAudit", "payload": {"id": "older", "activityDisplayName": "older"}}
    declared = {"records": [fixture]}
    snapshot = {"records": [fixture, older]}
    client = Mock()
    client.get.return_value.json.return_value = snapshot
    assert collector_expectations({"all": True}, declared, client, "/tenant") == snapshot
    client.get.assert_called_once_with(
        "/tenant/evidence/export", params={"family": "directoryAudit"}
    )
    client.reset_mock()
    assert collector_expectations({}, declared, client, "/tenant") is declared
    client.get.assert_not_called()


def test_target_or_expectations_cannot_hide_missing_declared_fixture(monkeypatch):
    import pytest

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    from qualify_evidence_collectors import collector_expectations

    client = Mock()
    client.get.return_value.json.return_value = {"records": []}
    with pytest.raises(AssertionError):
        collector_expectations(
            {"all": True},
            {"records": [{"family": "directoryAudit", "payload": {"id": "fixture"}}]},
            client,
            "/tenant",
        )

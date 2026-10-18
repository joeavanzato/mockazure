"""Cloud qualification must reserve its expanded local endpoint range."""

from unittest.mock import patch

from tools import verify_default


def test_default_ports_exclude_all_data_origins():
    assert hasattr(verify_default, "default_ports"), (
        "Port planning must account for six API listeners"
    )
    with patch.object(verify_default, "free_port", side_effect=[61000, 61001, 61005, 61006]):
        assert verify_default.default_ports() == (61000, 61006)


def test_management_matrix_requires_four_actual_clients():
    import copy
    import json
    from pathlib import Path

    import pytest

    from tools.verify_cloud_workflows import cases, validate_report

    assert "B" in cases(), "ARM management matrix is absent"
    definition = cases()["B"]
    assert definition["notApplicable"] == {}
    manifest = json.loads(Path("integrations/cloud-workflows-manifest.json").read_text())
    report = {
        "phase": "B",
        "sourceCommit": "a" * 40,
        "sourceFingerprint": "b" * 64,
        "ecosystems": {},
    }
    for ecosystem in ("python", "powershell", "dotnet", "typescript"):
        report["ecosystems"][ecosystem] = {
            "status": "passed",
            "clientVersions": manifest["phaseClients"]["B"][ecosystem],
            "cases": [
                {
                    "workflow": row["id"],
                    "operationIds": row["operationIds"],
                    "transport": row.get("transportByEcosystem", {}).get(
                        ecosystem, row["transport"]
                    ),
                    "expectedStatus": "completed",
                    "actualStatus": "completed",
                    "assertions": {name: True for name in row["assertions"]},
                }
                for row in definition["workflows"]
                if ecosystem in row["ecosystems"]
            ],
        }
    validate_report(report, set(report["ecosystems"]))
    missing = copy.deepcopy(report)
    del missing["ecosystems"]["dotnet"]
    with pytest.raises(ValueError):
        validate_report(missing, set(report["ecosystems"]))
    fallback = copy.deepcopy(report)
    fallback["ecosystems"]["typescript"]["cases"][0]["transport"] = "rest"
    with pytest.raises(ValueError):
        validate_report(fallback, set(report["ecosystems"]))


def test_async_protocol_source_is_hash_bound():
    import hashlib
    import json
    from pathlib import Path

    value = json.loads(Path("integrations/contracts/cloud/arm-async-protocol.json").read_text())
    assert hashlib.sha256(Path(value["localPath"]).read_bytes()).hexdigest() == value["sha256"]
    assert value["revision"] and value["envelopes"]["asyncStatus"]["required"] == ["status"]

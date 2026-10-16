"""Qualification is evidence from real pinned clients, not a fabricated fixture."""

import json
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx


def test_current_graph_distribution_qualification():
    root = Path(__file__).resolve().parents[2]
    instance = root / ".runtime/m3/instance"
    assert (instance / "ca.pem").is_file(), "Prepare the dedicated M3 instance first"
    with httpx.Client(
        verify=ssl.create_default_context(cafile=str(instance / "ca.pem")), trust_env=False
    ) as client:
        assert client.get("https://localhost:48443/_mock/ready").status_code == 200
    from tools.prepare_clients import prepare
    from tools.verify_graph import sha, source_fingerprint

    prepare(root / "integrations/manifest.json", root / "work/vendor", verify_only=True)
    path = root / "work/graph-directory-report.json"
    assert path.is_file(), "Run tools/verify_graph.py against the dedicated M3 deployment"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 1
    assert datetime.now(UTC) - datetime.fromisoformat(report["qualified_at"]) < timedelta(days=1)
    assert report["source_fingerprint"] == source_fingerprint()
    assert report["manifest_sha256"] == sha(root / "integrations/manifest.json")
    assert report["lock_sha256"] == sha(root / "uv.lock")
    assert report["wheel_sha256"] == sha(next((root / "dist").glob("mockazure-*.whl")))
    required = {
        "snapshot_5000_concurrent_mutation",
        "two_api_replicas",
        "api_restart",
        "postgres_restart",
        "continuation_replay",
        "delta_resume",
        "cross_tenant_reference_denied",
        "wrong_audience_denied",
        "shared_429_recovery",
        "clean_wheel_install",
        "native_cli_dataset",
        "native_network_blocked",
        "container_python_network_blocked",
        "container_powershell_network_blocked",
    }
    assert set(report["checks"]) == required
    assert all(value is True for value in report["checks"].values())
    assert set(report["clients"]) == {"native_python", "container_python", "container_powershell"}
    for label, result in report["clients"].items():
        assert result["passed"], label
        assert set(result["modes"]) == {"application", "delegated"}
        for mode, workflow in result["modes"].items():
            assert workflow["seed_user_count"] == 5000, (label, mode)
            assert workflow["seed_first_id"] == report["seed_first_id"]
            for gate in (
                "create",
                "profile_update",
                "membership",
                "delta",
                "delete",
                "permission_denied",
            ):
                assert workflow[gate] is True, (label, mode, gate)

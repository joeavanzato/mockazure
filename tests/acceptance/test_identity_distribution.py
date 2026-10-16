import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.verify_identity import ROOT, sha, source_fingerprint


def test_current_identity_distribution_qualification():
    path = Path("work/identity-report.json")
    assert path.is_file(), "Run tools/verify_identity.py against the dedicated M2 deployment"
    report = json.loads(path.read_text())
    assert report["schema_version"] == 1
    assert datetime.now(UTC) - datetime.fromisoformat(report["qualified_at"]) < timedelta(days=1)
    assert report["source_fingerprint"] == source_fingerprint(), (
        "Source changed after qualification"
    )
    assert report["manifest_sha256"] == sha(ROOT / "integrations/manifest.json")
    assert report["lock_sha256"] == sha(ROOT / "uv.lock")
    assert report["wheel_sha256"] == sha(next((ROOT / "dist").glob("mockazure-*.whl")))
    assert all(report["replication"].values()) and len(report["replication"]) == 8
    required = {
        "python/network.json",
        "powershell/network.json",
        "python/app-only.json",
        "python/delegated.json",
        "powershell/app-only.json",
        "powershell/delegated.json",
        "powershell/exchange-bootstrap.json",
        "powershell/arm-bootstrap.json",
        "powershell/collectors.json",
        "powershell/denied-connections.json",
    }
    assert set(report["probes"]) == required
    for name, probe in report["probes"].items():
        if name.endswith("network.json"):
            assert all(probe[key] for key in ("dns_blocked", "ipv4_blocked", "ipv6_blocked"))
        else:
            assert probe.get("passed", probe.get("graph_passed", False)), name
    assert (
        report["probes"]["powershell/collectors.json"]["organization_id"]
        == (report["expected_tenant_id"])
    )
    assert (
        report["probes"]["powershell/collectors.json"]["subscription_id"]
        == (report["expected_subscription_id"])
    )

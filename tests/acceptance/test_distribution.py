import json
from pathlib import Path


def test_live_qualification_report():
    path = Path("work/foundation-report.json")
    assert path.is_file(), "Run tools/verify_foundation.py against the built deployment first"
    report = json.loads(path.read_text())
    for key in (
        "tls_verified",
        "tenant_survived_recreate",
        "admin_token_unchanged",
        "two_api_processes",
        "two_workers_no_duplicate_exports",
        "wheel_install_passed",
        "native_external_blocked",
        "container_external_blocked",
        "docker_only_bootstrap",
    ):
        assert report[key] is True, key
    assert report["ca_fingerprint_before"] == report["ca_fingerprint_after"]
    assert report["external_service_requests"] == 0

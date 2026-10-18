"""Run the coverage program on one fresh local lab, with a second isolation tenant."""

import hashlib
import importlib
import json
from datetime import UTC, datetime

from tools.benchmark_coverage import qualify as collections
from tools.prepare_clients import prepare
from tools.verify_default import ROOT
from tools.verify_default import main as smoke

STAGES = (
    "directory_coverage",
    "exchange_collections",
    "mail_content",
    "site_drive",
    "teams",
    "intune",
    "activity_feed",
    "method_management",
    "authentication_policies",
)
VENDORS = (
    ("manifest.json", "vendor"),
    ("mail-content-manifest.json", "mail-content-vendor"),
    ("site-drive-manifest.json", "site-drive-vendor"),
    ("teams-manifest.json", "teams-vendor"),
    ("intune-manifest.json", "intune-clients-vendor"),
    ("method-management-manifest.json", "method-management-clients-vendor"),
)


def qualify(directory, command, compose, client, headers, tenant, check):
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "passed": False,
        "stages": [],
        "manifests": {},
    }
    path = directory / "coverage-program-report.json"

    def save():
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    save()
    try:
        for manifest, vendor in VENDORS:
            source = ROOT / "integrations" / manifest
            prepare(source, ROOT / "work" / vendor, verify_only=True)
            report["manifests"][manifest] = {
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "clients": json.loads(source.read_text(encoding="utf-8")),
            }
        for name in STAGES:
            check("program_begin_" + name)
            importlib.import_module("tools.verify_" + name).qualify(
                directory, command, compose, client, headers, tenant, check
            )
            report["stages"].append(name)
            save()
        # Deterministic IDs and bytes, two tenant boundaries, retries and actual restart.
        collections(directory, command, compose, client, headers, tenant, check, records=404, mib=2)
        report["stages"].append("two_tenant_collection_recovery")
        report["outputs"] = {
            str(output.relative_to(directory)): hashlib.sha256(output.read_bytes()).hexdigest()
            for output in sorted(directory.rglob("*report.json"))
            if output != path and output.name != "report.json"
        }
        for manifest, vendor in VENDORS:
            prepare(ROOT / "integrations" / manifest, ROOT / "work" / vendor, verify_only=True)
        report["passed"] = True
        check("whole_program_original_clients_and_unchanged_artifacts")
    finally:
        save()


if __name__ == "__main__":
    smoke(qualify=qualify)

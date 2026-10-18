"""Run explicit native-client workflows locally; never reduce a missing matrix to a pass."""

import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

from mockazure.cloud.contracts import load_contract
from tools.verify_default import ROOT

ECOSYSTEMS = {"python", "powershell", "dotnet", "typescript"}


def cases():
    return json.loads((ROOT / "integrations/cloud-workflows-cases.json").read_text())["phases"]


def validate_report(report, required_ecosystems):
    phase = report["phase"]
    definitions = cases()
    if phase not in definitions or phase == "all":
        raise ValueError("Phase is not yet qualified")
    if set(report["ecosystems"]) != required_ecosystems or required_ecosystems != ECOSYSTEMS:
        raise ValueError("The complete four-ecosystem matrix is required")
    if not re.fullmatch("[0-9a-f]{40}", report.get("sourceCommit", "")) or not re.fullmatch(
        "[0-9a-f]{64}", report.get("sourceFingerprint", "")
    ):
        raise ValueError("Missing source identity")
    profile = definitions[phase]
    for ecosystem, entry in report["ecosystems"].items():
        expected = [row for row in profile["workflows"] if ecosystem in row["ecosystems"]]
        if not expected:
            if (
                ecosystem not in profile["notApplicable"]
                or entry.get("status") != "not-applicable"
                or not entry.get("reason")
            ):
                raise ValueError("Missing explicit not-applicable cell")
            continue
        if entry.get("status") != "passed" or not entry.get("clientVersions"):
            raise ValueError("Native client execution is missing")
        manifest = json.loads((ROOT / "integrations/cloud-workflows-manifest.json").read_text())
        if entry["clientVersions"] != manifest["clients"].get(ecosystem):
            raise ValueError("Unpinned native client version")
        actual = entry.get("cases", [])
        if len(actual) != len(expected) or {r["workflow"] for r in actual} != {
            r["id"] for r in expected
        }:
            raise ValueError("Missing or duplicated workflow")
        for definition in expected:
            result = next(row for row in actual if row["workflow"] == definition["id"])
            if (
                result["transport"] != definition["transport"]
                or set(result["operationIds"]) != set(definition["operationIds"])
                or result["expectedStatus"] != result["actualStatus"]
                or not result.get("assertions")
                or any(value is not True for value in result["assertions"].values())
                or not set(definition["assertions"]) <= result["assertions"].keys()
            ):
                raise ValueError("Native workflow assertions failed")
            for operation in result["operationIds"]:
                load_contract(definition["service"], operation)


def source_identity():
    paths = [
        path
        for directory in ("src", "tools", "integrations")
        for path in (ROOT / directory).rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]
    paths.extend(
        ROOT / name for name in ("Dockerfile", "compose.yaml", "pyproject.toml", "uv.lock")
    )
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    return commit, digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=[*list("ABCDEF"), "all"])
    parser.add_argument("--ecosystems", nargs="+", required=True, choices=sorted(ECOSYSTEMS))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.phase != "A" or set(args.ecosystems) != ECOSYSTEMS:
        parser.error(
            "Only the current A1-A3 Exchange matrix is executable; later phases remain unavailable"
        )
    from tools.prepare_clients import prepare
    from tools.verify_default import main as smoke
    from tools.verify_exchange_workflows import qualify

    started = time.monotonic()
    commit, fingerprint = source_identity()
    report = {
        "phase": args.phase,
        "sourceCommit": commit,
        "sourceFingerprint": fingerprint,
        "passed": False,
        "ecosystems": {},
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)

    def run(directory, *rest):
        qualify(directory, *rest)
        observed = json.loads((directory / "exchange-workflow-report.json").read_text())
        assert observed["passed"] and observed["records"] == 1101
        report["nativeReport"] = str(directory / "exchange-workflow-report.json")
        report["ecosystems"] = {
            key: {"status": "not-applicable", "reason": reason}
            for key, reason in cases()["A"]["notApplicable"].items()
        } | {
            "powershell": {
                "status": "passed",
                "clientVersions": {"ExchangeOnlineManagement": observed["version"]},
                "transportAdaptations": [
                    "Local TLS origin and local bearer token",
                    "Server-generated bounded command proxies via original module bootstrap",
                    "Local port 443 TCP relay for the original module bootstrap",
                ],
                "cases": [
                    {
                        "workflow": "exchange.security-configuration",
                        "operationIds": next(
                            v["operationIds"]
                            for v in cases()["A"]["workflows"]
                            if v["id"] == "exchange.security-configuration"
                        ),
                        "transport": "module",
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {
                            k: observed["security_configuration"]
                            for k in (
                                "six_families_roundtrip",
                                "audit_ingestion_toggle",
                                "configuration_absence",
                            )
                        },
                    },
                    {
                        "workflow": "exchange.unified-audit",
                        "operationIds": ["Search-UnifiedAuditLog"],
                        "transport": "module",
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {
                            "exact_ids": True,
                            "paging": observed["page_sizes"] == [500, 500, 101, 0],
                            "utc_dates": observed["utc_unspecified_dates"],
                            "formatted": True,
                        },
                    },
                    {
                        "workflow": "exchange.recipients-and-delegation",
                        "operationIds": [
                            "Get-Recipient",
                            "Get-EXORecipient",
                            "Get-EXOMailboxPermission",
                            "Get-EXORecipientPermission",
                            "Add-MailboxPermission",
                            "Remove-MailboxPermission",
                            "Add-RecipientPermission",
                            "Remove-RecipientPermission",
                            "Set-Mailbox",
                        ],
                        "transport": "module",
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {
                            "identity_selectors": observed["recipient_delegation"],
                            "paging": observed["recipient_count"] == 2,
                            "permission_mutations": observed["recipient_delegation"],
                            "behalf_mutations": observed["recipient_delegation"],
                            "revoked_permissions_absent": observed["recipient_delegation"],
                        },
                    },
                ],
            },
        }

    try:
        prepare(ROOT / "integrations/manifest.json", ROOT / "work/vendor", verify_only=True)
        smoke(qualify=run)
        if source_identity() != (commit, fingerprint):
            raise ValueError("Source changed during qualification")
        validate_report(report, set(args.ecosystems))
        report["passed"] = True
    finally:
        report["elapsedSeconds"] = round(time.monotonic() - started, 3)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

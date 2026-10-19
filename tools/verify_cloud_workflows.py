"""Run explicit native-client workflows locally; never reduce a missing matrix to a pass."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
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
    if phase == "all":
        phases = report.get("phases", [])
        if (
            required_ecosystems != ECOSYSTEMS
            or len(phases) != 6
            or {p.get("phase") for p in phases} != set("ABCDEF")
        ):
            raise ValueError("The complete six-phase matrix is required")
        for child in phases:
            if child.get("passed") is not True or any(
                child.get(k) != report.get(k) for k in ("sourceCommit", "sourceFingerprint")
            ):
                raise ValueError("Phase evidence failed or differs from aggregate source")
            validate_report(child, required_ecosystems)
        return
    if phase not in definitions:
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
        expected_versions = manifest.get("phaseClients", {}).get(phase, manifest["clients"])
        if entry["clientVersions"] != expected_versions.get(ecosystem):
            raise ValueError("Unpinned native client version")
        actual = entry.get("cases", [])
        if len(actual) != len(expected) or {r["workflow"] for r in actual} != {
            r["id"] for r in expected
        }:
            raise ValueError("Missing or duplicated workflow")
        for definition in expected:
            result = next(row for row in actual if row["workflow"] == definition["id"])
            if (
                result["transport"]
                != definition.get("transportByEcosystem", {}).get(
                    ecosystem, definition["transport"]
                )
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


def run_structured(path):
    from tools.prepare_structured_clients import prepare
    from tools.verify_default import main as smoke
    from tools.verify_structured_services import qualify

    started = time.monotonic()
    commit, fingerprint = source_identity()
    report = {
        "phase": "D",
        "sourceCommit": commit,
        "sourceFingerprint": fingerprint,
        "passed": False,
        "ecosystems": {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)

    def run(*args):
        observed = qualify(*args)
        assert observed["passed"] and observed["localOnly"]
        report["nativeReport"] = str(args[0] / "structured-services-report.json")
        report["crossRead"] = observed["crossRead"]
        for ecosystem, entry in observed["written"].items():
            assertions = dict(entry["assertions"])
            assertions.update(
                api_worker_restart=observed["restarted"][ecosystem]["assertions"]["persistentRead"],
                cloud_restore=observed["restored"][ecosystem]["assertions"]["persistentRead"]
                and observed["restored"][ecosystem]["assertions"]["secretBackupRestore"],
                key_rotation=observed["oldKeyDenied"],
                metadataOnlySnapshot=observed["metadataOnlySnapshot"],
                cross_language_reads=all(
                    v["passed"] and v["objects"] == 12 for v in observed["crossRead"].values()
                ),
            )
            rows = []
            for definition in cases()["D"]["workflows"]:
                if ecosystem not in definition["ecosystems"]:
                    continue
                rows.append(
                    {
                        "workflow": definition["id"],
                        "operationIds": definition["operationIds"],
                        "transport": definition.get("transportByEcosystem", {}).get(
                            ecosystem, definition["transport"]
                        ),
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {name: assertions[name] for name in definition["assertions"]},
                    }
                )
            report["ecosystems"][ecosystem] = {
                "status": "passed",
                "clientVersions": entry["clientVersions"],
                "restFallbacks": entry.get("restFallbacks", []),
                "cases": rows,
            }
        validate_report(report, ECOSYSTEMS)

    try:
        prepare()
        smoke(qualify=run)
        if source_identity() != (commit, fingerprint):
            raise ValueError("Runtime source changed during qualification")
        report["passed"] = True
    finally:
        report["elapsedSeconds"] = time.monotonic() - started
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def run_management(path):
    from tools.prepare_cloud_clients import prepare
    from tools.verify_cloud_management import qualify
    from tools.verify_default import main as smoke

    started = time.monotonic()
    commit, fingerprint = source_identity()
    report = {
        "phase": "B",
        "sourceCommit": commit,
        "sourceFingerprint": fingerprint,
        "passed": False,
        "ecosystems": {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)

    def run(*args):
        observed = qualify(*args)
        report["nativeReport"] = str(args[0] / "cloud-management-report.json")
        definition = cases()["B"]["workflows"][0]
        for ecosystem, entry in observed["clients"].items():
            assert entry["passed"] and entry["cleanup"]["passed"]
            assert (
                entry["assertions"]["created"] == 12
                and entry["cleanup"]["assertions"]["deleted"] == 12
            )
            cross = (
                entry["cross_read"]["assertions"]["cross_reads"]
                if ecosystem == "python"
                else entry["assertions"]["cross_reads"]
            )
            assert cross == 12 and entry["assertions"]["updated"] is True
            report["ecosystems"][ecosystem] = {
                "status": "passed",
                "clientVersions": entry["clientVersions"],
                "transportAdaptations": [
                    "Explicit local HTTPS origin and instance CA",
                    "Local signed bearer via configured TokenCredential or Az custom environment",
                    "Generic ARM SDK/module with actual per-provider api-version",
                    "TypeScript and Az Get-AzResource IDs omit the client-supplied separator",
                    ".NET uses Network SDK child models and full-resource PUT for VM tag update",
                ],
                "cases": [
                    {
                        "workflow": definition["id"],
                        "operationIds": definition["operationIds"],
                        "transport": entry["transport"],
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {name: True for name in definition["assertions"]},
                    }
                ],
            }
        validate_report(report, ECOSYSTEMS)

    try:
        prepare()
        smoke(qualify=run)
        if source_identity() != (commit, fingerprint):
            raise ValueError("Runtime source changed during qualification")
        report["passed"] = True
    finally:
        report["elapsedSeconds"] = time.monotonic() - started
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def run_storage(path):
    from tools.prepare_storage_clients import prepare
    from tools.verify_default import main as smoke
    from tools.verify_storage_binary import qualify

    started = time.monotonic()
    commit, fingerprint = source_identity()
    report = {
        "phase": "C",
        "sourceCommit": commit,
        "sourceFingerprint": fingerprint,
        "passed": False,
        "ecosystems": {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)

    def run(*args):
        observed = qualify(*args)
        assert observed["passed"] and observed["prior"]["passed"]
        assert observed["crossRead"]["objects"] == 8 and observed["revoked"]["oldKeyDenied"]
        definition = cases()["C"]["workflows"][0]
        for ecosystem, written in observed["written"].items():
            assert (
                written["passed"]
                and observed["restarted"][ecosystem]["passed"]
                and observed["restored"][ecosystem]["passed"]
            )
            report["ecosystems"][ecosystem] = {
                "status": "passed",
                "clientVersions": written["clientVersions"],
                "transportAdaptations": [
                    "Explicit local HTTPS endpoints and instance CA",
                    "Loopback-IP path addressing for original .NET/Az.Storage URI builders",
                    "2019-07-07 legacy writes and selected 2025-01-05 reads for Az.Storage",
                ],
                "cases": [
                    {
                        "workflow": definition["id"],
                        "operationIds": definition["operationIds"],
                        "transport": "module" if ecosystem == "powershell" else "sdk",
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {name: True for name in definition["assertions"]},
                    }
                ],
            }
        report["nativeReport"] = str(args[0] / "storage-binary-report.json")
        report["pythonBytesPerObject"] = observed["written"]["python"]["objects"]["blob"]["bytes"]
        assert report["pythonBytesPerObject"] == 268435456
        validate_report(report, ECOSYSTEMS)

    try:
        prepare()
        smoke(qualify=run)
        if source_identity() != (commit, fingerprint):
            raise ValueError("Runtime source changed during qualification")
        report["passed"] = True
    finally:
        report["elapsedSeconds"] = time.monotonic() - started
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def run_graph(path):
    from tools.prepare_graph_workflow_clients import prepare
    from tools.verify_default import main as smoke
    from tools.verify_graph_workflows import qualify

    started = time.monotonic()
    commit, fingerprint = source_identity()
    report = {
        "phase": "E",
        "sourceCommit": commit,
        "sourceFingerprint": fingerprint,
        "passed": False,
        "ecosystems": {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)

    def run(*args):
        observed = qualify(*args)
        assert (
            observed["passed"] and observed["localOnly"] and observed["timezonePlatformEquivalence"]
        )
        report["nativeReport"] = str(args[0] / "graph-workflows-report.json")
        report["timezonePlatformEquivalence"] = True
        for ecosystem, entry in observed["written"].items():
            assertions = {
                **entry["assertions"],
                **observed["restarted"][ecosystem]["assertions"],
                **{
                    key: observed[key]
                    for key in ("callback_delivery", "callback_retry", "revoked_callbacks")
                },
            }
            report["ecosystems"][ecosystem] = {
                "status": "passed",
                "clientVersions": entry["clientVersions"],
                "restFallbacks": entry.get("restFallbacks", []),
                "cases": [
                    {
                        "workflow": row["id"],
                        "operationIds": row["operationIds"],
                        "transport": row.get("transportByEcosystem", {}).get(
                            ecosystem, row["transport"]
                        ),
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {key: assertions[key] for key in row["assertions"]},
                    }
                    for row in cases()["E"]["workflows"]
                ],
            }
        validate_report(report, ECOSYSTEMS)

    try:
        prepare()
        smoke(qualify=run)
        if source_identity() != (commit, fingerprint):
            raise ValueError("Runtime source changed during qualification")
        report["passed"] = True
    finally:
        report["elapsedSeconds"] = time.monotonic() - started
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def run_queries(path):
    from tools.prepare_query_clients import prepare
    from tools.verify_cloud_queries import qualify
    from tools.verify_default import main as smoke

    started = time.monotonic()
    commit, fingerprint = source_identity()
    report = {
        "phase": "F",
        "sourceCommit": commit,
        "sourceFingerprint": fingerprint,
        "passed": False,
        "ecosystems": {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)

    def run(*args):
        observed = qualify(*args)
        assert observed["passed"] and observed["localOnly"] and observed["legacy_batch_rest"]
        report["nativeReport"] = str(args[0] / "cloud-queries-report.json")
        report["legacyBatchRest"] = True
        for ecosystem, entry in observed["written"].items():
            assertions = {
                **entry["assertions"],
                **observed["restarted"][ecosystem]["assertions"],
                "restart_recovery": all(observed["restarted"][ecosystem]["assertions"].values()),
            }
            report["ecosystems"][ecosystem] = {
                "status": "passed",
                "clientVersions": entry["clientVersions"],
                "restFallbacks": entry.get("restFallbacks", []),
                "cases": [
                    {
                        "workflow": row["id"],
                        "operationIds": row["operationIds"],
                        "transport": row.get("transportByEcosystem", {}).get(
                            ecosystem, row["transport"]
                        ),
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {key: assertions[key] for key in row["assertions"]},
                    }
                    for row in cases()["F"]["workflows"]
                ],
            }
        validate_report(report, ECOSYSTEMS)

    try:
        prepare()
        smoke(qualify=run)
        if source_identity() != (commit, fingerprint):
            raise ValueError("Runtime source changed during qualification")
        report["passed"] = True
    finally:
        report["elapsedSeconds"] = time.monotonic() - started
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def run_all(path):
    """Run every original-client profile against one frozen source identity."""
    started = time.monotonic()
    commit, fingerprint = source_identity()
    report = {
        "phase": "all",
        "sourceCommit": commit,
        "sourceFingerprint": fingerprint,
        "passed": False,
        "phases": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    evidence = path.parent / (path.stem + "-phases")
    evidence.mkdir(exist_ok=True)
    try:
        for phase in "ABCDEF":
            if source_identity() != (commit, fingerprint):
                raise ValueError("Source changed during aggregate qualification")
            child = evidence / (phase + ".json")
            log = evidence / (phase + ".log")
            print(f"Qualifying phase {phase}; log: {log}", flush=True)
            with log.open("w", encoding="utf-8") as stream:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-X",
                        "utf8",
                        "-m",
                        "tools.verify_cloud_workflows",
                        "--phase",
                        phase,
                        "--ecosystems",
                        *sorted(ECOSYSTEMS),
                        "--report",
                        str(child.resolve()),
                    ],
                    cwd=ROOT,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            if completed.returncode:
                raise RuntimeError(f"Phase {phase} failed; inspect {log}")
            value = json.loads(child.read_text(encoding="utf-8"))
            report["phases"].append(value)
            print(f"Phase {phase} completed", flush=True)
        validate_report(report, ECOSYSTEMS)
        if source_identity() != (commit, fingerprint):
            raise ValueError("Source changed during aggregate qualification")
        report["passed"] = True
    finally:
        report["elapsedSeconds"] = round(time.monotonic() - started, 3)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=[*list("ABCDEF"), "all"])
    parser.add_argument("--ecosystems", nargs="+", required=True, choices=sorted(ECOSYSTEMS))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.phase == "all" and set(args.ecosystems) == ECOSYSTEMS:
        run_all(args.report)
        return
    if args.phase == "F" and set(args.ecosystems) == ECOSYSTEMS:
        run_queries(args.report)
        return
    if args.phase == "E" and set(args.ecosystems) == ECOSYSTEMS:
        run_graph(args.report)
        return
    if args.phase == "C" and set(args.ecosystems) == ECOSYSTEMS:
        run_storage(args.report)
        return
    if args.phase == "D" and set(args.ecosystems) == ECOSYSTEMS:
        run_structured(args.report)
        return
    if args.phase == "B" and set(args.ecosystems) == ECOSYSTEMS:
        run_management(args.report)
        return
    if args.phase != "A" or set(args.ecosystems) != ECOSYSTEMS:
        parser.error("The complete four-ecosystem matrix is required")
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
                        "workflow": "exchange.role-management",
                        "operationIds": next(
                            v["operationIds"]
                            for v in cases()["A"]["workflows"]
                            if v["id"] == "exchange.role-management"
                        ),
                        "transport": "module",
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {
                            k: observed["role_management"]
                            for k in ("role_groups", "membership_roundtrip", "assignment_roundtrip")
                        },
                    },
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

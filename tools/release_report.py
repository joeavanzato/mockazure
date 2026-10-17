"""A release cannot pass on incomplete suites, stale sources or reduced scale targets."""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

STAGES = ("m3", "m3b", "m3c", "m4", "m5")
SCALE_TARGETS = {"small": (100, 1000), "medium": (5000, 50000), "large": (100000, 1000000)}
ROOT = Path(__file__).resolve().parents[1]


def source_fingerprint(root=ROOT):
    files = {
        root / name
        for name in ("pyproject.toml", "uv.lock", "Dockerfile", "README.md", "LICENSE", "NOTICE")
    }
    files.update(root.glob("compose*.yaml"))
    for directory in ("src", "tools", "integrations", "deploy"):
        files.update(
            p
            for p in (root / directory).rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and p.suffix in {".py", ".ps1", ".json", ".yaml", ".cfg", ".md"}
        )
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def failed_cases(value):
    failed = []
    collectors = value.get("collectors", {})
    if "collector" in value:
        collectors = {"arm": value["collector"]}
    if not isinstance(collectors, dict):
        return ["invalid collector report"]
    for name, collector in collectors.items():
        if not isinstance(collector, dict):
            continue
        for case in collector.get("cases", []):
            if case.get("passed") is not True:
                failed.append(
                    f"{name}/{case.get('mode', 'unknown')}/"
                    + ("denied" if case.get("denied") else "allowed")
                )
    return failed


def release_readiness(report, fingerprint):
    failures = []
    try:
        assert report["schema_version"] == 1
        assert report["source_fingerprint"] == fingerprint
        age = datetime.now(UTC) - datetime.fromisoformat(report["qualified_at"])
        assert timedelta(0) <= age < timedelta(days=1)
    except (AssertionError, KeyError, TypeError, ValueError):
        failures.append("report identity, source or freshness")
    suites = report.get("suites", {})
    for stage in STAGES:
        suite = suites.get(stage, {})
        if not (
            suite.get("status") == "passed"
            and suite.get("validated") is True
            and type(suite.get("exit_code")) is int
            and suite["exit_code"] == 0
            and re.fullmatch("[0-9a-f]{64}", suite.get("report_sha256", ""))
            and suite.get("failed_cases") == []
        ):
            failures.append("suite " + stage)
    qualification_ready = not failures
    for name, (users, records) in SCALE_TARGETS.items():
        value = report.get("scale", {}).get(name, {})
        if not (
            value.get("passed") is True
            and value.get("users") == users
            and value.get("records") == records
        ):
            failures.append("scale " + name)
    artifacts = report.get("artifacts", {})
    if not all(artifacts.get(key) is True for key in ("passed", "reproducible", "parity")):
        failures.append("candidate artifact reproducibility/parity")
    return {
        "qualification_ready": qualification_ready,
        "release_ready": not failures,
        "failed_gates": failures,
    }

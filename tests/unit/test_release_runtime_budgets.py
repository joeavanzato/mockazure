"""Observed valid runtimes must fit bounded orchestration allowances."""

import json
import re
import subprocess
from pathlib import Path


def test_previously_measured_evidence_suite_can_finish(monkeypatch, tmp_path):
    from tools import verify_release as verifier

    monkeypatch.setenv("MOCKAZURE_QUALIFICATION_PROFILE", "release")
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    monkeypatch.setattr(verifier, "LOGS", tmp_path / "reports")
    monkeypatch.setattr(verifier, "source_fingerprint", lambda: "a" * 64)
    monkeypatch.setattr(verifier, "capture", lambda args: "b" * 40)
    monkeypatch.setattr(verifier, "prepare_suite", lambda stage: None)
    monkeypatch.setattr(verifier, "validate_suite", lambda stage, path: None)
    monkeypatch.setattr(verifier, "validate_release", lambda report: {"release_ready": False})

    def original_run(args, label, **kwargs):
        if label == "m3b-qualification":
            # Existing unmodified original-client baseline, docs/compatibility.md.
            if kwargs["timeout"] < 2842.423:
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            Path(args[-1]).write_text(json.dumps({"collectors": {}}))
        return 0

    monkeypatch.setattr(verifier, "run", original_run)
    result = verifier.run_suites(tmp_path / "report.json", ["m3b"])
    assert result["suites"]["m3b"]["status"] == "passed"


def test_all_m6_databases_allow_observed_recovery_and_graceful_shutdown():
    paths = [*Path.cwd().glob("compose.release.*.yaml"), Path("compose.test.m6.yaml")]
    for path in paths:
        text = path.read_text()
        checks = [line for line in text.splitlines() if "pg_isready" in line]
        assert len(checks) == 1 and "-h 127.0.0.1" in checks[0], path.name
        startup = re.search(r"start_period: (\d+)s", text)
        shutdown = re.search(r"stop_grace_period: (\d+)s", text)
        assert startup and 120 <= int(startup[1]) <= 300, path.name
        assert shutdown and 120 <= int(shutdown[1]) <= 300, path.name

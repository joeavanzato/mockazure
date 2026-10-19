import copy
from datetime import UTC, datetime

import pytest


def report():
    return {
        "schema_version": 1,
        "source_fingerprint": "a" * 64,
        "qualified_at": datetime.now(UTC).isoformat(),
        "suites": {
            stage: {
                "status": "passed",
                "validated": True,
                "exit_code": 0,
                "report_sha256": "b" * 64,
                "failed_cases": [],
            }
            for stage in ("m3", "m3b", "m3c", "m4", "m5")
        },
        "scale": {
            name: {"passed": True, "users": users, "records": records}
            for name, users, records in (
                ("small", 100, 1000),
                ("medium", 5000, 50000),
                ("large", 100000, 1000000),
            )
        },
        "artifacts": {"passed": True, "reproducible": True, "parity": True},
        "cloud": {
            name: {"passed": True, "validated": True, "report_sha256": "d" * 64}
            for name in ("matrix", "scale", "distribution", "coverage")
        },
    }


def test_release_readiness_rejects_missing_partial_and_blocked_gates():
    from tools.release_report import release_readiness

    good = report()
    assert release_readiness(good, "a" * 64)["release_ready"] is True
    for bad in (
        {},
        {**good, "source_fingerprint": "c" * 64},
        {**good, "qualified_at": "2000-01-01T00:00:00+00:00"},
        {**good, "qualified_at": "2099-01-01T00:00:00+00:00"},
    ):
        assert release_readiness(bad, "a" * 64)["release_ready"] is False
    for stage in good["suites"]:
        bad = copy.deepcopy(good)
        del bad["suites"][stage]
        assert release_readiness(bad, "a" * 64)["release_ready"] is False
    for changed in (
        {"validated": 1},
        {"status": "running"},
        {"exit_code": 1},
        {"failed_cases": ["application/hawk_rules", "delegated/hawk_rules"]},
    ):
        bad = copy.deepcopy(good)
        bad["suites"]["m4"].update(changed)
        assert release_readiness(bad, "a" * 64)["release_ready"] is False
    for size in (999999, 1000):
        bad = copy.deepcopy(good)
        bad["scale"]["large"]["records"] = size
        assert release_readiness(bad, "a" * 64)["release_ready"] is False
    bad = copy.deepcopy(good)
    bad["artifacts"]["reproducible"] = False
    assert release_readiness(bad, "a" * 64)["release_ready"] is False


def test_collect_failures_keeps_original_m4_case_identity():
    from tools.release_report import failed_cases

    cases = [
        {"mode": mode, "denied": False, "passed": False, "error": "bad output"}
        for mode in ("application", "delegated")
    ]
    value = {
        "collectors": {
            "hawk_rules": {"passed": False, "cases": cases},
            "mes_trace": {"passed": False, "cases": cases},
        }
    }
    assert set(failed_cases(value)) == {
        "hawk_rules/application/allowed",
        "hawk_rules/delegated/allowed",
        "mes_trace/application/allowed",
        "mes_trace/delegated/allowed",
    }


@pytest.mark.parametrize("stage", ["m3", "m3b", "m3c", "m4", "m5"])
def test_release_compose_configuration_cannot_reference_predecessor_state(monkeypatch, stage):
    from tools.qualification_profile import profile_for

    monkeypatch.setenv("MOCKAZURE_QUALIFICATION_PROFILE", "release")
    profile = profile_for(stage)
    from pathlib import Path

    for name in (profile.compose, profile.clients_compose):
        text = Path(name).read_text(encoding="utf-8")
        assert f".runtime/{stage}/" not in text
        assert f"name: mockazure-{stage}" not in text
        assert profile.runtime_dir in text and profile.project in text
    assert "external: true" in Path(profile.clients_compose).read_text(encoding="utf-8")
    assert profile.internal_subnet.startswith("10.231.")


def test_cli_returns_nonzero_for_an_incomplete_release(monkeypatch):
    import sys

    from tools import verify_release

    monkeypatch.setenv("MOCKAZURE_QUALIFICATION_PROFILE", "")
    monkeypatch.setenv("UV_CACHE_DIR", "work/test-cache")
    monkeypatch.setattr(sys, "argv", ["verify_release"])
    monkeypatch.setattr(verify_release, "run", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        verify_release,
        "run_suites",
        lambda *args: {
            "qualification_ready": False,
            "release_ready": False,
            "failed_gates": ["suite m4"],
        },
    )
    with pytest.raises(SystemExit) as stopped:
        verify_release.main()
    assert stopped.value.code == 2


@pytest.mark.parametrize("gate", ["scale", "artifacts"])
def test_combined_validation_rejects_unbacked_extra_gate_flags(monkeypatch, gate):
    from tools import verify_release

    monkeypatch.setattr(verify_release, "source_fingerprint", lambda: "a" * 64)
    value = {
        "schema_version": 1,
        "source_fingerprint": "a" * 64,
        "qualified_at": datetime.now(UTC).isoformat(),
        "suites": {},
        "scale": {"small": {"passed": True, "users": 100, "records": 1000}},
        "artifacts": {"passed": True, "reproducible": True, "parity": True},
    }
    verify_release.validate_release(value)
    candidate = value[gate]["small"] if gate == "scale" else value[gate]
    assert candidate["passed"] is False


def test_release_readiness_requires_cloud_program_gates():
    from tools.release_report import release_readiness

    value = report()
    value["cloud"] = {
        name: {"passed": True, "validated": True, "report_sha256": "d" * 64}
        for name in ("matrix", "scale", "distribution", "coverage")
    }
    assert release_readiness(value, "a" * 64)["release_ready"] is True
    for name in value["cloud"]:
        missing = copy.deepcopy(value)
        del missing["cloud"][name]
        assert release_readiness(missing, "a" * 64)["release_ready"] is False


@pytest.mark.parametrize("gate", ["matrix", "scale", "distribution", "coverage"])
def test_cloud_release_flags_require_backing_evidence(monkeypatch, gate):
    from tools import verify_release

    monkeypatch.setattr(verify_release, "source_fingerprint", lambda: "a" * 64)
    value = report()
    verify_release.validate_release(value)
    assert value["cloud"][gate]["validated"] is False

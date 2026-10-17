"""Scale success requires actual completed, source-bound recovery and coverage."""

from copy import deepcopy

import pytest


def valid():
    return {
        "name": "small",
        "seed": 61001,
        "users": 100,
        "records": 1000,
        "source_fingerprint": "a" * 64,
        "status": "complete",
        "passed": True,
        "imported": {"users": 100, "records": 1000},
        "published": 1000,
        "worker_restart": True,
        "api_restart": True,
        "restart_boundary": {"started_ns": 20, "completed_ns": 30},
        "reads": [
            {
                "family": family,
                "caller": caller,
                "count": count,
                "id_sha256_xor": ("b" if family == "user" else "c") * 64,
                "pages": 2,
                "restart": {
                    "captured_ns": 10,
                    "resumed_ns": 40,
                    "pages_before": 1,
                    "pages_after": 1,
                },
                "seconds": 1,
                "p95_seconds": 0.1,
            }
            for family, count in (("user", 100), ("signIn", 1000))
            for caller in (0, 1)
        ],
    }


def test_completed_scale_report():
    from tools.benchmark_release import validate_profile

    validate_profile(valid(), "a" * 64)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "running"),
        ("passed", 1),
        ("users", 99),
        ("records", 999),
        ("published", 999),
        ("worker_restart", False),
        ("api_restart", False),
        ("source_fingerprint", "d" * 64),
        ("imported", {"users": 100, "records": 999}),
        ("reads", []),
    ],
)
def test_incomplete_scale_never_passes(field, value):
    from tools.benchmark_release import validate_profile

    report = valid()
    report[field] = value
    with pytest.raises((AssertionError, ValueError)):
        validate_profile(report, "a" * 64)


def test_mismatched_or_duplicated_callers_fail():
    from tools.benchmark_release import validate_profile

    report = valid()
    report["reads"][1]["id_sha256_xor"] = "d" * 64
    with pytest.raises(AssertionError):
        validate_profile(report, "a" * 64)
    report = deepcopy(valid())
    report["reads"][1]["caller"] = 0
    with pytest.raises(AssertionError):
        validate_profile(report, "a" * 64)


@pytest.mark.parametrize(
    "url",
    [
        "//graph.microsoft.com/v1.0/users",
        "https://graph.microsoft.com/v1.0/users",
        "http://localhost:46443/v1.0/users",
        "/v1.0/users#fragment",
    ],
)
def test_scale_page_never_leaves_owned_https(monkeypatch, url):
    from tools.benchmark_release import require_page_url
    from tools.qualification_profile import profile_for

    monkeypatch.setenv("MOCKAZURE_QUALIFICATION_PROFILE", "release")
    with pytest.raises((AssertionError, ValueError)):
        require_page_url(profile_for("m5"), url)


@pytest.mark.parametrize("change", ["missing", "early_resume", "late_capture", "no_page_after"])
def test_report_requires_every_reader_to_cross_restart(change):
    from tools.benchmark_release import validate_profile

    value = valid()
    evidence = value["reads"][-1]
    if change == "missing":
        del evidence["restart"]
    elif change == "early_resume":
        evidence["restart"]["resumed_ns"] = 19
    elif change == "late_capture":
        evidence["restart"]["captured_ns"] = 31
    else:
        evidence["restart"]["pages_after"] = 0
    with pytest.raises((AssertionError, KeyError)):
        validate_profile(value, "a" * 64)

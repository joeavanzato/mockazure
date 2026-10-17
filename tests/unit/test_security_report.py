"""A successful process is insufficient evidence for a security qualification."""

import copy
from datetime import UTC, datetime, timedelta

import pytest

from tools.verify_security import CLIENT_CHECKS, RUNTIME_CHECKS, validate_results


def valid():
    return {
        "schema_version": 1,
        "passed": True,
        "qualified_at": datetime.now(UTC).isoformat(),
        "checks": dict.fromkeys(RUNTIME_CHECKS, True),
        "clients": {
            name: {
                "passed": True,
                "modes": {
                    mode: dict.fromkeys(CLIENT_CHECKS, True)
                    for mode in ("application", "delegated")
                },
            }
            for name in ("native_python", "container_python", "container_powershell")
        },
        "collectors": {
            name: {
                "passed": True,
                "records_verified": 1,
                "source_hashes": {"original.ps1": "a" * 64},
                "output_hashes": {"output.csv": "b" * 64},
                "cases": [
                    {"mode": mode, "denied": denied, "passed": True}
                    for mode in ("application", "delegated")
                    for denied in (False, True)
                ],
            }
            for name in (
                "hawk_credentials",
                "mes_policies",
                "mes_grants",
                "mes_methods",
                "mes_roles",
            )
        },
    }


def test_security_report_rejects_missing_or_stale_checks():
    baseline = valid()
    validate_results(baseline)
    for key in RUNTIME_CHECKS:
        report = copy.deepcopy(baseline)
        del report["checks"][key]
        with pytest.raises((AssertionError, ValueError)):
            validate_results(report)
    for name in baseline["clients"]:
        for mode in ("application", "delegated"):
            for key in CLIENT_CHECKS:
                report = copy.deepcopy(baseline)
                report["clients"][name]["modes"][mode][key] = False
                with pytest.raises((AssertionError, ValueError)):
                    validate_results(report)
    for hours in (-1, 25):
        report = copy.deepcopy(baseline)
        report["qualified_at"] = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        with pytest.raises((AssertionError, ValueError)):
            validate_results(report)


def test_security_report_rejects_exit_only_collector_success():
    for name in valid()["collectors"]:
        report = valid()
        report["collectors"][name] = {"passed": True}
        with pytest.raises((AssertionError, KeyError, ValueError)):
            validate_results(report)
        report = valid()
        report["collectors"][name]["records_verified"] = 0
        with pytest.raises((AssertionError, ValueError)):
            validate_results(report)


def test_security_report_requires_collector_outputs_and_both_denial_modes():
    for name in valid()["collectors"]:
        report = valid()
        report["collectors"][name]["output_hashes"] = {}
        with pytest.raises(AssertionError):
            validate_results(report)
        report = valid()
        report["collectors"][name]["cases"].pop()
        with pytest.raises(AssertionError):
            validate_results(report)
        report = valid()
        report["collectors"][name]["cases"][0]["passed"] = False
        with pytest.raises(AssertionError):
            validate_results(report)

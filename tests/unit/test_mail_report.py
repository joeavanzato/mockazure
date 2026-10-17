import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


def validator():
    assert Path("tools/verify_mail.py").is_file(), "Mail qualification validator is missing"
    from tools.verify_mail import CLIENT_CHECKS, EXCHANGE_CHECKS, RUNTIME_CHECKS, validate_results

    return CLIENT_CHECKS, EXCHANGE_CHECKS, RUNTIME_CHECKS, validate_results


def test_mail_report_fails_closed():
    clients, exchange, runtime, validate = validator()
    baseline = {
        "schema_version": 1,
        "passed": True,
        "qualified_at": datetime.now(UTC).isoformat(),
        "checks": dict.fromkeys(runtime, True),
        "clients": {
            profile: {
                "passed": True,
                "modes": {
                    mode: dict.fromkeys(clients, True) for mode in ("application", "delegated")
                },
            }
            for profile in ("native_python", "container_python", "container_powershell")
        },
        "exchange": {
            "passed": True,
            "version": "3.11.0",
            "modes": {mode: dict.fromkeys(exchange, True) for mode in ("application", "delegated")},
        },
    }
    from tools.qualify_mail_collectors import FUNCTIONS

    baseline["collectors"] = {
        label: {
            "passed": True,
            "records_verified": 1,
            "source_hashes": {"original.ps1": "test"},
            "output_hashes": {"output.csv": "test"},
            "cases": [
                {"mode": mode, "denied": denied, "passed": True}
                for mode in ("application", "delegated")
                for denied in (False, True)
            ],
        }
        for label in FUNCTIONS
    }
    validate(baseline)
    patched = copy.deepcopy(baseline)
    patched["collector_profile"] = "serialization-v1"
    patched["upstream_baseline"] = {}
    for name, item in patched["collectors"].items():
        item["profile"] = "serialization-v1"
        if name in {"hawk_rules", "mes_trace"}:
            original = copy.deepcopy(item)
            original.update(profile="original", passed=False)
            for case in original["cases"]:
                case["passed"] = case["denied"]
                if not case["denied"]:
                    case["failure"] = "Known serializer failure"
            patched["upstream_baseline"][name] = original
    validate(patched)
    for mutate in ("missing_baseline", "mixed_profile", "hidden_failure", "fake_upstream"):
        bad = copy.deepcopy(patched)
        if mutate == "missing_baseline":
            bad["upstream_baseline"].pop("hawk_rules")
        elif mutate == "mixed_profile":
            bad["collectors"]["hawk_rules"]["profile"] = "original"
        elif mutate == "hidden_failure":
            bad["collectors"]["hawk_rules"]["cases"][0]["passed"] = False
        else:
            bad["upstream_baseline"]["hawk_rules"]["profile"] = "serialization-v1"
        with pytest.raises(AssertionError):
            validate(bad)
    for label in FUNCTIONS:
        bad = copy.deepcopy(baseline)
        bad["collectors"][label]["passed"] = 1
        with pytest.raises(AssertionError):
            validate(bad)
        bad = copy.deepcopy(baseline)
        del bad["collectors"][label]
        with pytest.raises(AssertionError):
            validate(bad)
    bad = copy.deepcopy(baseline)
    bad["collectors"]["hawk_rules"]["cases"][0]["passed"] = False
    bad["collectors"]["hawk_rules"]["cases"][0]["failure"] = "JSONDecodeError: Extra data"
    with pytest.raises(AssertionError):
        validate(bad)
    bad["passed"] = False
    with pytest.raises(AssertionError):
        validate(bad)
    for field in runtime:
        bad = copy.deepcopy(baseline)
        bad["checks"][field] = 1
        with pytest.raises(AssertionError):
            validate(bad)
    for profile in baseline["clients"]:
        for mode in ("application", "delegated"):
            for field in clients:
                bad = copy.deepcopy(baseline)
                del bad["clients"][profile]["modes"][mode][field]
                with pytest.raises(AssertionError):
                    validate(bad)
    for mode in ("application", "delegated"):
        for field in exchange:
            bad = copy.deepcopy(baseline)
            bad["exchange"]["modes"][mode][field] = False
            with pytest.raises(AssertionError):
                validate(bad)
    for hours in (-1, 25):
        bad = copy.deepcopy(baseline)
        bad["qualified_at"] = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        with pytest.raises(AssertionError):
            validate(bad)


def test_mail_report_requires_current_identity_and_bytes(tmp_path):
    validator()
    from tools.verify_mail import validate_report

    with pytest.raises((AssertionError, KeyError)):
        validate_report({"passed": True}, root=tmp_path)

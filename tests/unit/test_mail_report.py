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
    validate(baseline)
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

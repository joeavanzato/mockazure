import copy
import hashlib
import json
from datetime import UTC, datetime

import pytest


def test_arm_report_required_gates_and_identity():
    from tools.verify_arm import CLIENT_CHECKS, RUNTIME_CHECKS, validate_results

    cases = [
        {
            "mode": mode,
            "discovery": discovery,
            "denied": denied,
            "passed": True,
            "native_records": 0 if denied else 2,
        }
        for mode in ("application", "delegated")
        for discovery in ("discovered", "explicit")
        for denied in (False, True)
    ]
    report = {
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
        "collector": {
            "passed": True,
            "records_verified": 1003,
            "cases": cases,
            "source_hashes": {"original.ps1": "test"},
            "output_hashes": {"out.json": "test"},
        },
    }
    validate_results(report)
    for name in RUNTIME_CHECKS:
        bad = copy.deepcopy(report)
        del bad["checks"][name]
        with pytest.raises((AssertionError, KeyError)):
            validate_results(bad)
    for name in report["clients"]:
        bad = copy.deepcopy(report)
        bad["clients"][name]["modes"]["application"]["activity_paging"] = 1
        with pytest.raises(AssertionError):
            validate_results(bad)
    bad = copy.deepcopy(report)
    bad["collector"]["cases"][0]["passed"] = False
    with pytest.raises(AssertionError):
        validate_results(bad)
    bad = copy.deepcopy(report)
    bad["collector"]["cases"].pop()
    with pytest.raises(AssertionError):
        validate_results(bad)


def test_arm_collector_exact_artifacts_and_no_repair(tmp_path):
    from tools.qualify_arm_collectors import verify_artifacts

    expected = [
        {
            "id": "/subscriptions/local/events/one/ticks/1",
            "eventDataId": "one",
            "eventTimestamp": "2026-10-01T00:00:00.0000001Z",
            "submissionTimestamp": "2026-10-01T00:00:01.0000002Z",
            "subscriptionId": "local",
            "resourceId": "/subscriptions/local",
            "operationName": {"value": "test/read"},
            "status": {"value": "Succeeded"},
            "correlationId": "correlation",
        }
    ]
    path = tmp_path / "ActivityLog.json"
    path.write_text(json.dumps(expected), encoding="utf-8")
    assert verify_artifacts([path], expected) == 1
    for rows in (
        [],
        expected * 2,
        [{**expected[0], "eventTimestamp": "2026-10-01T00:00:00Z"}],
        [{k: v for k, v in expected[0].items() if k != "correlationId"}],
    ):
        path.write_text(json.dumps(rows), encoding="utf-8")
        with pytest.raises(AssertionError):
            verify_artifacts([path], expected)
    path.write_text(json.dumps(expected) + json.dumps(expected), encoding="utf-8")
    with pytest.raises(ValueError):
        verify_artifacts([path], expected)
    path.write_text(json.dumps(expected), encoding="utf-8")
    with pytest.raises(AssertionError):
        verify_artifacts([path], [], denied=True)
    assert verify_artifacts([], [], denied=True) == 0
    with pytest.raises(AssertionError):
        verify_artifacts([], expected)


def test_arm_artifact_hashes_reject_escape_and_changes(tmp_path):
    from tools.qualify_arm_collectors import validate_hashes

    root = tmp_path / "allowed"
    root.mkdir()
    item = root / "artifact.json"
    item.write_text("[]", encoding="utf-8")
    good = {"artifact.json": hashlib.sha256(item.read_bytes()).hexdigest()}
    validate_hashes(good, root)
    for invalid in (
        {},
        {"../artifact.json": "0" * 64},
        {str(item): "0" * 64},
        {"artifact.json": "0" * 64},
    ):
        with pytest.raises(AssertionError):
            validate_hashes(invalid, root)


def test_arm_source_fingerprint_binds_packaged_metadata(tmp_path):
    from tools.verify_arm import source_fingerprint

    names = (
        "pyproject.toml",
        "uv.lock",
        "build-requirements.txt",
        "Dockerfile",
        "integrations/Dockerfile.python",
        "compose.m5.yaml",
        "compose.clients.m5.yaml",
        "deploy/haproxy-m5.cfg",
        "README.md",
        "LICENSE",
        "NOTICE",
    )
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original", encoding="utf-8")
    baseline = source_fingerprint(tmp_path)
    for name in ("README.md", "LICENSE", "NOTICE", "build-requirements.txt"):
        path = tmp_path / name
        path.write_text("changed", encoding="utf-8")
        assert source_fingerprint(tmp_path) != baseline, name
        path.write_text("original", encoding="utf-8")


def test_denied_collector_error_is_only_exact_explicit_lookup():
    from tools.qualify_arm_collectors import validate_denied_error

    sub = "2cd50efb-9d14-430b-82aa-98fef2b7cbc1"
    message = (
        f"Subscription {sub} was not found in tenant . "
        "Please verify that the subscription exists in this tenant."
    )
    valid = {"error_type": "System.Management.Automation.PSArgumentException", "error": message}
    validate_denied_error(valid, "explicit", sub)
    for discovery in ("explicit", "discovered"):
        validate_denied_error({"error": None}, discovery, sub)
    for report, discovery in (
        (valid, "discovered"),
        ({**valid, "error_type": "System.Management.Automation.RuntimeException"}, "explicit"),
        ({**valid, "error": "Subscription response could not be deserialized"}, "explicit"),
        ({**valid, "error": "403 forbidden"}, "explicit"),
        ({**valid, "error": message + " Unexpected transport failure"}, "explicit"),
        (
            {**valid, "error": message.replace(sub, "00000000-0000-0000-0000-000000000000")},
            "explicit",
        ),
    ):
        with pytest.raises(AssertionError):
            validate_denied_error(report, discovery, sub)

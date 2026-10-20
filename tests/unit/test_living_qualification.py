from copy import deepcopy

import pytest

from tools.verify_living_tenant import validate_report


def complete():
    return {
        "phase": "content",
        "scale": "small",
        "passed": True,
        "source_fingerprint": "a" * 64,
        "cases": {
            "population": {"state": "passed"},
            "graph": {"state": "passed", "users": 20},
            "storage": {"state": "passed", "sha256": "b" * 64, "bytes": 123},
            "file": {"state": "passed", "sha256": "b" * 64, "bytes": 123},
            "queue": {"state": "passed"},
            "table": {"state": "passed"},
            "exchange": {
                "state": "passed",
                "mailboxes": 20,
                "commands": ["Get-Mailbox", "Get-EXOMailbox -Identity"],
            },
            "replay": {"state": "passed", "created": 0},
        },
    }


def test_content_report_requires_all_original_clients_and_matching_hashes():
    report = complete()
    validate_report(report)
    for family in report["cases"]:
        incomplete = deepcopy(report)
        del incomplete["cases"][family]
        with pytest.raises(ValueError):
            validate_report(incomplete)
    report["cases"]["file"]["sha256"] = "c" * 64
    with pytest.raises(ValueError):
        validate_report(report)


def test_failed_or_incorrect_collection_cannot_be_a_success():
    for family, key, value in [
        ("exchange", "mailboxes", 19),
        ("graph", "users", 21),
        ("replay", "created", 1),
        ("queue", "state", "skipped"),
    ]:
        report = complete()
        report["cases"][family][key] = value
        with pytest.raises(ValueError):
            validate_report(report)


def test_combined_report_rejects_missing_phase_and_hunting_gate():
    from tools.living_hunting import validate

    with pytest.raises(ValueError):
        validate({"phase": "hunting", "passed": True, "cases": {}, "failed_cases": []})
    with pytest.raises((ValueError, AssertionError)):
        validate_report({"phase": "all", "passed": True, "phases": {"content": complete()}})

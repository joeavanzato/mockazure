"""Support claims need explicit implementation and observed behavior evidence."""

import copy

import pytest


def test_coverage_index_requires_behavior_evidence():
    from tools.check_cloud_coverage import validate_entry

    entry = {
        "operationId": "example",
        "status": "implemented",
        "handler": "exists",
        "tests": ["behavior.py"],
        "qualification": [],
    }
    validate_entry(
        entry,
        implemented=True,
        test_exists=lambda p: p == "behavior.py",
        qualified_operations=set(),
    )
    for change in (
        {"status": "qualified"},
        {"tests": []},
        {"status": "planned", "qualification": ["native-run"]},
    ):
        invalid = copy.deepcopy(entry) | change
        with pytest.raises(ValueError):
            validate_entry(
                invalid, implemented=True, test_exists=lambda p: True, qualified_operations=set()
            )
    with pytest.raises(ValueError):
        validate_entry(
            entry, implemented=False, test_exists=lambda p: True, qualified_operations=set()
        )


def test_cloud_coverage_index_matches_operation_catalogs():
    from tools.check_cloud_coverage import check

    check()

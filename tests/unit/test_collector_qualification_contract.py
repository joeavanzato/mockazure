import pytest


def test_missing_collector_profiles_cannot_qualify(tmp_path):
    from tools.evidence_collector_reports import validate_qualification

    for report in (
        {"status": "passed", "profiles": {}},
        {"status": "passed", "profiles": {"00-hawk-application": {"passed": True}}},
    ):
        with pytest.raises(AssertionError):
            validate_qualification(report, tmp_path)


def test_denial_cannot_hide_source_drift(tmp_path):
    from tools.evidence_collector_reports import valid_denial

    assert not valid_denial({"source_integrity": False, "output_count": 0}, [{"status": 403}])
    assert not valid_denial({"source_integrity": True, "output_count": 1}, [{"status": 403}])
    assert valid_denial({"source_integrity": True, "output_count": 0}, [{"status": 403}])

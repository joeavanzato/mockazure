from unittest.mock import create_autospec


def test_combined_verifier_calls_real_collector_signature(monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    import qualify_evidence_collectors

    from tools import verify_evidence

    qualifier = create_autospec(
        qualify_evidence_collectors.qualify_collectors,
        return_value={"status": "failed", "profiles": {}},
    )
    monkeypatch.setattr(qualify_evidence_collectors, "qualify_collectors", qualifier)
    result = verify_evidence.run_collector_qualification()
    qualifier.assert_called_once_with()
    assert result["status"] == "failed"

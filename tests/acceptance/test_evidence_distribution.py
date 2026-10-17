"""Only a fresh qualification of the real isolated deployment can satisfy this gate."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path


def test_current_evidence_distribution_qualification():
    root = Path(__file__).resolve().parents[2]
    assert (root / "tools/verify_evidence.py").is_file(), "Evidence verifier is required"
    from tools.verify_evidence import validate_report

    path = root / "work/evidence-report.json"
    assert path.is_file(), "Run tools/verify_evidence.py against the isolated M3B deployment"
    report = json.loads(path.read_text())
    assert datetime.now(UTC) - datetime.fromisoformat(report["qualified_at"]) < timedelta(days=1)
    validate_report(report, root=root)

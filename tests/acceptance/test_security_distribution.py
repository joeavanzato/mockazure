import json
from pathlib import Path


def test_current_security_distribution_qualification():
    root = Path(__file__).resolve().parents[2]
    from tools.verify_security import validate_report

    path = root / "work/security-report.json"
    assert path.is_file(), "Run tools/verify_security.py on the isolated M3C runtime"
    validate_report(json.loads(path.read_text()), root=root)

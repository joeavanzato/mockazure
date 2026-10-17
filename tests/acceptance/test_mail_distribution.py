import json
from pathlib import Path


def test_current_mail_distribution_qualification():
    root = Path(__file__).resolve().parents[2]
    path = root / "work/mail-report.json"
    assert path.is_file(), "Run tools/verify_mail.py against the isolated M4 runtime"
    from tools.verify_mail import validate_report

    validate_report(json.loads(path.read_text()), root=root)

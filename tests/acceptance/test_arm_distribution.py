import json
from pathlib import Path


def test_current_arm_distribution_qualification():
    from tools.verify_arm import validate_report

    root = Path(__file__).resolve().parents[2]
    path = root / "work/arm-report.json"
    assert path.is_file(), "Run tools/verify_arm.py against the isolated M5 runtime"
    validate_report(json.loads(path.read_text(encoding="utf-8")), root=root)

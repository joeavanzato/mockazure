"""A candidate artifact pass requires matching current-source archive and parity evidence."""

import json
from pathlib import Path


def test_current_candidate_artifact_reproducibility_and_parity():
    from tools.build_release import validate_artifact_report
    from tools.release_report import source_fingerprint

    root = Path(__file__).resolve().parents[2]
    path = root / "work/release/artifacts.json"
    assert path.is_file(), "Run tools.build_release for the committed candidate first"
    assert validate_artifact_report(
        json.loads(path.read_text(encoding="utf-8")), root, source_fingerprint()
    )

"""The final combined release gate stays red while any mandatory work is unresolved."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_current_release_distribution_and_artifact_gates():
    root = Path(__file__).resolve().parents[2]
    path = root / "work/release/qualification.json"
    assert path.is_file(), "Run the current-source M6 qualification"
    script = (
        "import json; from pathlib import Path; "
        "from tools.verify_release import validate_release; "
        "report=json.loads(Path('work/release/qualification.json').read_text(encoding='utf-8')); "
        "result=validate_release(report); print(json.dumps(result)); "
        "assert result['release_ready'] is True, result['failed_gates']"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env={**os.environ, "MOCKAZURE_QUALIFICATION_PROFILE": "release"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["release_ready"] is True

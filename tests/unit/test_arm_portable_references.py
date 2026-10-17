import hashlib
import json
from pathlib import Path


def test_derived_contract_bytes_survive_git_line_endings():
    data = Path("src/mockazure/providers/arm/data")
    manifest = json.loads((data / "references.json").read_text(encoding="utf-8"))
    for name in ("roles.json", "operations.json"):
        raw = (data / name).read_bytes()
        assert b"\r\n" not in raw, "Hash-bound authored metadata must use canonical LF"
        assert hashlib.sha256(raw).hexdigest() == manifest["files"]["derived:" + name]["sha256"]

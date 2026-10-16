"""Verify source provenance and release archive boundaries."""

import hashlib
import json
import tarfile
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "reference/provenance.json").read_text())
for item in manifest["files"]:
    assert (
        hashlib.sha256((root / "reference" / item["destination_path"]).read_bytes()).hexdigest()
        == item["sha256"]
    )
for path in [*(root / "dist").glob("*.whl"), *(root / "dist").glob("*.tar.gz")]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    else:
        with tarfile.open(path) as archive:
            names = archive.getnames()
    assert not any(
        part in {"reference", "tests", ".runtime", "work"}
        for name in names
        for part in name.split("/")
    )
print("Provenance and distribution boundaries verified")

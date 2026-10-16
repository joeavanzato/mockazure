"""Copy only the four approved, tracked Git blobs; never execute source code."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ALLOWLIST = (
    "lab/mockcloud/server.py",
    "lab/mockcloud/certs.py",
    "lab/mockcloud/__main__.py",
    "tools/synth/cloud.py",
)


def git(source: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(source), *args])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    revision = git(args.source, "rev-parse", "--verify", args.ref + "^{commit}").decode().strip()
    files, writes = [], []
    for name in ALLOWLIST:
        blob = git(args.source, "show", f"{revision}:{name}")
        relative = "mockcloud/" + name
        files.append(
            {
                "source_path": name,
                "destination_path": relative,
                "sha256": hashlib.sha256(blob).hexdigest(),
                "git_blob_id": git(args.source, "rev-parse", f"{revision}:{name}").decode().strip(),
            }
        )
        writes.append((args.output / relative, blob))
    manifest = {"schema_version": 1, "source_revision": revision, "files": files}
    writes.append(
        (args.output / "provenance.json", (json.dumps(manifest, indent=2) + "\n").encode())
    )
    for path, blob in writes:
        if path.exists() and path.read_bytes() != blob:
            parser.error(f"refusing conflicting destination: {path}")
    for path, blob in writes:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(blob)
    print(f"Imported {len(files)} tracked blobs at {revision}")


if __name__ == "__main__":
    main()

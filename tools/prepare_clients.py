"""Acquire pinned public clients; verification mode never uses the network."""

import argparse
import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path


def beneath(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()) or target == root.resolve():
        raise ValueError("Artifact path is outside output directory")
    return target


def verify_tree(root: Path, expected: dict[Path, str]) -> None:
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError("Extracted client links are unsupported")
        if path.is_file():
            actual.add(path.resolve())
    if actual != set(expected):
        raise ValueError("Extracted client file inventory mismatch")
    for path, digest in expected.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Extracted client content mismatch")


def prepare(manifest: Path, output: Path, *, verify_only: bool = False) -> None:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data["schema_version"] != 1:
        raise ValueError("Unsupported client manifest")
    trees: dict[Path, dict[Path, str]] = {}
    for item in data["artifacts"]:
        archive = beneath(output, item["archive"])
        if not archive.exists():
            if verify_only:
                raise ValueError("Missing pinned client archive")
            archive.parent.mkdir(parents=True, exist_ok=True)
            request = urllib.request.Request(
                item["source"], headers={"User-Agent": "MockAzure-client-preparation"}
            )
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = response.read()
        else:
            raw = archive.read_bytes()
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("Client SHA256 mismatch")
        if not verify_only:
            archive.write_bytes(raw)
        target = beneath(output, item["extracted"])
        # Verify the complete mounted tree, including unpinned sibling versions
        # that PowerShell's module discovery could select automatically.
        tree = beneath(output, target.relative_to(output.resolve()).parts[0])
        expected = trees.setdefault(tree, {})
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                path = beneath(target, member.filename)
                if member.external_attr >> 16 & 0o170000 == 0o120000:
                    raise ValueError("Archive symlinks are unsupported")
                if not member.is_dir():
                    if path in expected:
                        raise ValueError("Duplicate extracted client path")
                    expected[path] = hashlib.sha256(bundle.read(member)).hexdigest()
            if not verify_only:
                bundle.extractall(target)
    for tree, expected in trees.items():
        verify_tree(tree, expected)
    print(f"Verified {len(data['artifacts'])} pinned client archives and extracted trees")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("work/vendor"))
    parser.add_argument("--manifest", type=Path, default=Path("integrations/manifest.json"))
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    prepare(args.manifest, args.output, verify_only=args.verify)

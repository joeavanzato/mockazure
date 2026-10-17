"""Explicit, byte-pinned collector source patches; never repairs output artifacts."""

import hashlib
import io
import json
import zipfile
from pathlib import Path, PurePosixPath

from tools.prepare_clients import beneath, verify_tree

PROFILES = ("original", "serialization-v1")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def profile_root(root, profile):
    if profile not in PROFILES:
        raise ValueError("Unknown collector profile")
    return root / ("work/vendor" if profile == "original" else f"work/collector-profiles/{profile}")


def expected_files(root, profile):
    if profile not in PROFILES:
        raise ValueError("Unknown collector profile")
    manifest = json.loads((root / "integrations/manifest.json").read_text(encoding="utf-8"))
    files = {}
    for artifact in manifest["artifacts"]:
        if not artifact["extracted"].startswith("collectors/"):
            continue
        archive = beneath(root / "work/vendor", artifact["archive"])
        raw = archive.read_bytes()
        if digest(raw) != artifact["sha256"]:
            raise ValueError("Collector archive hash mismatch")
        with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
            for member in bundle.infolist():
                parts = PurePosixPath(member.filename)
                if (
                    parts.is_absolute()
                    or ".." in parts.parts
                    or "\\" in member.filename
                    or member.external_attr >> 16 & 0o170000 == 0o120000
                ):
                    raise ValueError("Unsafe collector archive member")
                if member.is_dir():
                    continue
                relative = (PurePosixPath(artifact["extracted"]) / parts).as_posix()
                if relative in files:
                    raise ValueError("Duplicate collector archive member")
                files[relative] = bundle.read(member)
    if not files:
        raise ValueError("Missing collector archives")
    # Ensure the unmodified baseline remains exactly the pinned upstream bytes.
    original = profile_root(root, "original")
    verify_tree(
        original / "collectors",
        {beneath(original, name): digest(raw) for name, raw in files.items()},
    )
    if profile == "original":
        return files
    patch = json.loads((root / f"integrations/collector-patches/{profile}.json").read_text())
    if patch["schema_version"] != 1 or patch["profile"] != profile:
        raise ValueError("Unsupported collector patch manifest")
    seen = set()
    for item in patch["patches"]:
        path = item["path"]
        if path not in files or path in seen:
            raise ValueError("Unknown or duplicate collector patch path")
        seen.add(path)
        raw = files[path]
        before, after = item["before"].encode("utf-8"), item["after"].encode("utf-8")
        if digest(raw) != item["before_sha256"] or not before or raw.count(before) != 1:
            raise ValueError("Collector patch source mismatch")
        raw = raw.replace(before, after, 1)
        if digest(raw) != item["after_sha256"]:
            raise ValueError("Collector patch output mismatch")
        files[path] = raw
    if not seen:
        raise ValueError("Empty collector patch profile")
    return files


def validate_profile(root: Path, profile: str) -> Path:
    """Read-only verification: reject missing, altered, extra or linked source files."""
    target = profile_root(root, profile)
    files = expected_files(root, profile)
    if target.is_symlink() or target.is_junction():
        raise ValueError("Linked collector profile")
    tree = target / "collectors" if profile == "original" else target
    verify_tree(tree, {beneath(target, name): digest(raw) for name, raw in files.items()})
    return target


def prepare_profile(root: Path, profile: str) -> Path:
    target = profile_root(root, profile)
    files = expected_files(root, profile)
    if profile != "original" and not target.exists():
        # Create a new profile only. Existing trees must validate; never repair tampering.
        target.mkdir(parents=True, exist_ok=False)
        for name, raw in files.items():
            path = beneath(target, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(raw)
    return validate_profile(root, profile)

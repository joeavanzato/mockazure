"""Offline, hash-bound Microsoft contract references; no runtime network access."""

import hashlib
import json
import re
from pathlib import Path

DATA = Path(__file__).parent / "data"


def _catalog(service):
    if not isinstance(service, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", service):
        raise ValueError("Invalid contract service")
    path = DATA / service / "operations.json"
    if not path.is_file():
        raise ValueError("Unsupported contract service")
    return json.loads(path.read_text("utf-8"))


def load_contract(service: str, operation_id: str) -> dict:
    catalog = _catalog(service)
    matches = [row for row in catalog["operations"] if row["operationId"] == operation_id]
    if len(matches) != 1:
        raise ValueError("Unsupported or duplicate operation")
    return matches[0]


def verify_references(service: str) -> None:
    catalog = _catalog(service)
    root = (DATA / service).resolve()
    ids = set()
    for row in catalog["operations"]:
        if row["operationId"] in ids or not row["sources"]:
            raise ValueError("Duplicate operation or missing source")
        ids.add(row["operationId"])
        for source in row["sources"]:
            relative = Path(source["localPath"])
            target = (root / relative).resolve()
            if relative.is_absolute() or not target.is_relative_to(root) or target == root:
                raise ValueError("Contract source path escapes root")
            if not source.get("revision") or not source.get("license"):
                raise ValueError("Contract provenance is incomplete")
            if (
                not target.is_file()
                or hashlib.sha256(target.read_bytes()).hexdigest() != source["sha256"]
            ):
                raise ValueError("Contract source hash mismatch")

"""Verify original collector bytes; reject malformed or incomplete outputs."""

import csv
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def records(path, format):
    text = Path(path).read_text(encoding="utf-8-sig")
    if format == "CSV":
        import io

        reader = csv.DictReader(io.StringIO(text), strict=True)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("Invalid CSV header")
        values = list(reader)
        if any(None in row or None in row.values() for row in values):
            raise ValueError("Invalid CSV fields")
    elif format == "SOF-ELK":
        values = [json.loads(line) for line in text.splitlines() if line.strip()]
    elif format == "JSON":
        value = json.loads(text)
        values = value if isinstance(value, list) else [value]
    else:
        raise ValueError("Unsupported output format")
    if any(not isinstance(row, dict) for row in values):
        raise ValueError("Expected record objects")
    return [{key.casefold(): value for key, value in row.items()} for row in values]


def verify_outputs(paths, *, format, expected, source_hashes):
    errors, outputs, seen = [], [], set()
    for source, digest in source_hashes.items():
        if not Path(source).is_file() or sha(source) != digest:
            errors.append("Original source hash differs: " + str(source))
    for item in paths:
        path = Path(item)
        try:
            rows = records(path, format)
            ids = []
            for row in rows:
                identifier = row.get("id")
                if not isinstance(identifier, str) or identifier not in expected:
                    errors.append("Unexpected or missing record ID")
                    continue
                ids.append(identifier)
                if identifier in seen:
                    errors.append("Duplicate record ID: " + identifier)
                seen.add(identifier)
                for field, value in expected[identifier].items():
                    if row.get(field.casefold()) != value:
                        errors.append("Sentinel field differs: " + identifier + "/" + field)
            outputs.append(
                {
                    "path": str(path),
                    "sha256": sha(path),
                    "record_ids": ids,
                    "record_count": len(rows),
                }
            )
        except (OSError, ValueError, csv.Error):
            errors.append("Invalid or unavailable " + format + " output: " + str(path))
    if seen != set(expected):
        errors.append("Output IDs differ from the expected set")
    return {
        "passed": not errors,
        "errors": errors,
        "outputs": outputs,
        "output_count": sum(item["record_count"] for item in outputs),
        "expected_ids": sorted(expected),
        "source_hashes": source_hashes,
    }


def valid_denial(profile, observations):
    return (
        profile.get("source_integrity") is True
        and profile.get("output_count") == 0
        and bool(observations)
        and all(row["status"] == 403 for row in observations)
    )


def validate_qualification(report, root):
    assert report["status"] == "passed"
    expected = {
        f"{offset + i:02d}-{'hawk' if i == 0 else 'mes'}-{mode}"
        for offset, mode in ((0, "application"), (10, "delegated"))
        for i in range(10)
    }
    assert set(report["profiles"]) == expected, "All20 collector profiles are required"
    for name, value in report["profiles"].items():
        assert value["passed"] is True and value["source_integrity"] is True
        assert value["source_hashes"], "Original collector source hashes are required"
        for source, digest in value["source_hashes"].items():
            path = (root / source).resolve()
            assert path.is_relative_to(root.resolve() / "work/vendor")
            assert sha(path) == digest
        for output in value["outputs"]:
            path = (root / output["path"]).resolve()
            assert path.is_relative_to(root.resolve() / ".runtime/m3b/clients/powershell-state")
            assert sha(path) == output["sha256"]
        slot = int(name.split("-")[0]) % 10
        if slot in (8, 9):
            assert value["expected_denial"] == ("permission" if slot == 8 else "licence")
            assert valid_denial(value, value["denial_observations"])
        elif slot == 7:
            assert value["expected_empty"] is True and value["output_count"] == 0
            assert all(check["passed"] for check in value["checks"].values())
        else:
            assert value["output_count"] > 0 and all(
                check["passed"] for check in value["checks"].values()
            )
        if slot in (0, 2):
            assert value["retry_observations"]
            assert all(
                r["status"] == 429 and r["continuation"] for r in value["retry_observations"]
            )

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
    elif format == "JSON_SEQUENCE_DIAGNOSTIC":
        documents = json_documents(text)
        values = [
            row
            for document in documents
            for row in (document if isinstance(document, list) else [document])
        ]
    elif format == "JSON":
        value = json.loads(text)
        values = value if isinstance(value, list) else [value]
    else:
        raise ValueError("Unsupported output format")
    if any(not isinstance(row, dict) for row in values):
        raise ValueError("Expected record objects")
    return [{key.casefold(): value for key, value in row.items()} for row in values]


def verify_outputs(paths, *, format, expected, source_hashes):
    errors, outputs, invalid_outputs, seen = [], [], [], set()
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
        except (OSError, ValueError, csv.Error) as error:
            try:
                digest = sha(path)
            except OSError:
                digest = None
            invalid_outputs.append(
                {"path": str(path), "sha256": digest, "error_type": type(error).__name__}
            )
            errors.append("Invalid or unavailable " + format + " output: " + str(path))
    if seen != set(expected):
        errors.append("Output IDs differ from the expected set")
    return {
        "passed": not errors,
        "errors": errors,
        "outputs": outputs,
        "invalid_outputs": invalid_outputs,
        "output_count": sum(item["record_count"] for item in outputs),
        "expected_ids": sorted(expected),
        "source_hashes": source_hashes,
    }


MES_JSON_LIMITATION = "mes-json-page-append"


def json_documents(text):
    documents, position = [], 0
    decoder = json.JSONDecoder()
    while position < len(text):
        while position < len(text) and text[position].isspace():
            position += 1
        if position == len(text):
            break
        document, position = decoder.raw_decode(text, position)
        documents.append(document)
    return documents


def diagnose_mes_json_collision(
    raw_paths, merged_paths, *, expected, source_hashes, merge_required=True
):
    """Diagnose original bytes only; this never makes their strict JSON check pass."""
    retrieval = verify_outputs(
        raw_paths, format="JSON_SEQUENCE_DIAGNOSTIC", expected=expected, source_hashes=source_hashes
    )
    collision, merge_matches = False, False
    try:
        raw_text = [Path(p).read_text(encoding="utf-8-sig") for p in sorted(raw_paths)]
        collision = any(len(json_documents(text)) > 1 for text in raw_text)
        if merge_required and len(merged_paths) == 1:
            # Exact pinned MergeOutput framing, including its original defect.
            # Compare in memory; never write a repaired or replacement output.
            pieces = []
            for text in raw_text:
                text = text.strip()
                if text.startswith("["):
                    text = text[1:]
                if text.endswith("]"):
                    text = text[:-1]
                if text.strip():
                    pieces.append(text.strip())
            original_merge = "[\n" + ",".join(pieces) + "]\n"
            merge_matches = Path(merged_paths[0]).read_text(encoding="utf-8-sig") == original_merge
        elif not merge_required and not merged_paths:
            merge_matches = True
    except (OSError, ValueError):
        pass
    return {
        "code": MES_JSON_LIMITATION,
        "diagnostic_only": True,
        "original_json_passed": False,
        "accepted": bool(expected) and retrieval["passed"] and collision and merge_matches,
        "retrieval": retrieval,
        "concatenated_documents": collision,
        "merged_matches_original": merge_matches,
    }


def accepted_profile(value):
    if value.get("passed") is True:
        return True
    case = value.get("parameters", {})
    limitation = value.get("known_limitation") or {}
    return (
        value.get("source_integrity") is True
        and value.get("exit_code") == 0
        and not value.get("error")
        and value.get("revision") == "47565f7842784cd3667ff630e3dccb49fa3408ed"
        and case.get("collector") == "mes"
        and case.get("function") == "Get-GraphEntraAuditLogs"
        and case.get("format") == "JSON"
        and limitation.get("code") == MES_JSON_LIMITATION
        and limitation.get("accepted") is True
        and limitation.get("original_json_passed") is False
    )


def valid_denial(profile, observations):
    return (
        profile.get("source_integrity") is True
        and profile.get("output_count") == 0
        and bool(observations)
        and all(row["status"] == 403 for row in observations)
    )


def validate_qualification(report, root):
    assert report["status"] in {"passed", "passed_with_known_limitations"}
    expected = {
        f"{offset + i:02d}-{'hawk' if i == 0 else 'mes'}-{mode}"
        for offset, mode in ((0, "application"), (10, "delegated"))
        for i in range(10)
    }
    assert set(report["profiles"]) == expected, "All20 collector profiles are required"
    for name, value in report["profiles"].items():
        assert accepted_profile(value) and value["source_integrity"] is True
        assert value["source_hashes"], "Original collector source hashes are required"
        for source, digest in value["source_hashes"].items():
            path = (root / source).resolve()
            assert path.is_relative_to(root.resolve() / "work/vendor")
            assert sha(path) == digest
        for output in [
            *value["outputs"],
            *value.get("invalid_outputs", []),
            value["expected_snapshot"],
        ]:
            path = (root / output["path"]).resolve()
            assert path.is_relative_to(root.resolve() / ".runtime/m3b/clients/powershell-state")
            assert sha(path) == output["sha256"]
        slot = int(name.split("-")[0]) % 10
        limitation = value.get("known_limitation")
        if value["passed"] is False:
            assert slot in (3, 5, 6) and limitation["accepted"]
            snapshot = json.loads((root / value["expected_snapshot"]["path"]).read_text())
            payloads = {r["payload"]["id"]: r["payload"] for r in snapshot["records"]}
            expected_values = {
                identifier: {"activityDisplayName": payloads[identifier]["activityDisplayName"]}
                for identifier in value["expected_ids"]
            }
            files = [root / o["path"] for o in [*value["outputs"], *value["invalid_outputs"]]]
            diagnosis = diagnose_mes_json_collision(
                [p for p in files if "Merged" not in p.parts],
                [p for p in files if "Merged" in p.parts],
                expected=expected_values,
                source_hashes={str(root / p): h for p, h in value["source_hashes"].items()},
                merge_required=value["parameters"]["merge"],
            )
            assert diagnosis["accepted"], (
                "Known MES defect must still preserve exact retrieved data"
            )
        elif slot in (8, 9):
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

    known = [name for name, value in report["profiles"].items() if value["passed"] is False]
    assert report["accepted_limitations"] == ({MES_JSON_LIMITATION: known} if known else {})
    assert report["status"] == ("passed_with_known_limitations" if known else "passed")

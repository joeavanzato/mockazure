import hashlib
import json

import pytest


def inputs(tmp_path):
    source = tmp_path / "Original.ps1"
    source.write_text("original")
    raw = tmp_path / "raw.json"
    raw.write_text(
        '[{"id":"one","activityDisplayName":"A"}]\n[{"id":"two","activityDisplayName":"B"}]\n'
    )
    merged = tmp_path / "merged.json"
    merged.write_text("[\n" + raw.read_text().strip()[1:-1].strip() + "]\n")
    expected = {"one": {"activityDisplayName": "A"}, "two": {"activityDisplayName": "B"}}
    hashes = {str(source): hashlib.sha256(source.read_bytes()).hexdigest()}
    return source, raw, merged, expected, hashes


def diagnose(raw, merged, expected, hashes):
    from tools.evidence_collector_reports import diagnose_mes_json_collision

    return diagnose_mes_json_collision([raw], [merged], expected=expected, source_hashes=hashes)


def test_known_collision_preserves_strict_failure_and_verifies_every_record(tmp_path):
    from tools.evidence_collector_reports import verify_outputs

    source, raw, merged, expected, hashes = inputs(tmp_path)
    before = (raw.read_bytes(), merged.read_bytes())
    assert not verify_outputs([raw], format="JSON", expected=expected, source_hashes=hashes)[
        "passed"
    ]
    result = diagnose(raw, merged, expected, hashes)
    assert result["accepted"] and result["diagnostic_only"]
    assert result["retrieval"]["output_count"] == 2
    assert result["original_json_passed"] is False
    assert (raw.read_bytes(), merged.read_bytes()) == before


@pytest.mark.parametrize(
    "damage", ["truncated", "missing", "duplicate", "sentinel", "source", "merge"]
)
def test_exception_cannot_accept_other_output_damage(tmp_path, damage):
    source, raw, merged, expected, hashes = inputs(tmp_path)
    if damage == "truncated":
        raw.write_text(raw.read_text()[:-5])
    elif damage == "missing":
        raw.write_text('[{"id":"one","activityDisplayName":"A"}]\n[]')
    elif damage == "duplicate":
        raw.write_text(raw.read_text() + raw.read_text())
    elif damage == "sentinel":
        raw.write_text(raw.read_text().replace('"B"', '"wrong"'))
    elif damage == "source":
        source.write_text("modified")
    else:
        merged.write_text("[]")
    assert not diagnose(raw, merged, expected, hashes)["accepted"]


def test_valid_json_needs_no_exception(tmp_path):
    source, raw, merged, expected, hashes = inputs(tmp_path)
    raw.write_text(json.dumps([{"id": k, **v} for k, v in expected.items()]))
    merged.write_text("[\n" + raw.read_text().strip()[1:-1].strip() + "]\n")
    assert not diagnose(raw, merged, expected, hashes)["accepted"]

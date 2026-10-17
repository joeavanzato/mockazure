import hashlib
import json

import pytest


def checker():
    from tools.evidence_collector_reports import verify_outputs

    return verify_outputs


def inputs(tmp_path):
    source = tmp_path / "Original.ps1"
    source.write_text("function Original { Get-MgAuditLogSignIn -All }")
    expected = {"one": {"ipAddress": "192.0.2.10"}, "two": {"ipAddress": "192.0.2.20"}}
    path = tmp_path / "records.json"
    path.write_text(json.dumps([{"id": key, **value} for key, value in expected.items()]))
    hashes = {str(source): hashlib.sha256(source.read_bytes()).hexdigest()}
    return path, source, hashes, expected


def test_complete_original_output_passes(tmp_path):
    path, source, hashes, expected = inputs(tmp_path)
    result = checker()([path], format="JSON", expected=expected, source_hashes=hashes)
    assert result["passed"] and result["output_count"] == 2
    assert result["outputs"][0]["record_ids"] == ["one", "two"]


@pytest.mark.parametrize(
    "damage",
    [
        "truncated",
        "overwritten",
        "concatenated",
        "duplicate",
        "unexpected",
        "sentinel",
        "source",
        "missing",
    ],
)
def test_collector_data_loss_is_failure(tmp_path, damage):
    path, source, hashes, expected = inputs(tmp_path)
    if damage == "truncated":
        path.write_text(path.read_text()[:-4])
    elif damage == "overwritten":
        path.write_text(json.dumps([{"id": "two", "ipAddress": "192.0.2.20"}]))
    elif damage == "concatenated":
        path.write_text(path.read_text() + path.read_text())
    elif damage == "duplicate":
        value = json.loads(path.read_text())
        path.write_text(json.dumps([*value, value[0]]))
    elif damage == "unexpected":
        value = json.loads(path.read_text())
        value.append({"id": "three"})
        path.write_text(json.dumps(value))
    elif damage == "sentinel":
        value = json.loads(path.read_text())
        value[0]["ipAddress"] = "192.0.2.99"
        path.write_text(json.dumps(value))
    elif damage == "source":
        source.write_text("changed original")
    elif damage == "missing":
        path.unlink()
    result = checker()([path], format="JSON", expected=expected, source_hashes=hashes)
    assert result["passed"] is False and result["errors"]


def test_duplicate_files_and_malformed_csv_fail(tmp_path):
    path, source, hashes, expected = inputs(tmp_path)
    assert not checker()([path, path], format="JSON", expected=expected, source_hashes=hashes)[
        "passed"
    ]
    path.write_text('id,ipAddress\n"one,192.0.2.10\n')
    assert not checker()([path], format="CSV", expected=expected, source_hashes=hashes)["passed"]


def test_hawk_case_and_sofelk_lines_are_preserved(tmp_path):
    path, source, hashes, expected = inputs(tmp_path)
    path.write_text("Id,IpAddress\none,192.0.2.10\ntwo,192.0.2.20\n")
    assert checker()([path], format="CSV", expected=expected, source_hashes=hashes)["passed"]
    path.write_text("\n".join(json.dumps({"id": key, **value}) for key, value in expected.items()))
    assert checker()([path], format="SOF-ELK", expected=expected, source_hashes=hashes)["passed"]
    path.write_text(path.read_text() + "\n" + "[]")
    assert not checker()([path], format="SOF-ELK", expected=expected, source_hashes=hashes)[
        "passed"
    ]


def test_empty_results_have_explicit_zero_gate(tmp_path):
    assert checker()([], format="JSON", expected={}, source_hashes={})["passed"]
    assert not checker()([], format="JSON", expected={"one": {}}, source_hashes={})["passed"]

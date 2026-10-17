import json

import pytest


def test_known_json_defect_requires_complete_concatenated_documents(tmp_path):
    from tools.qualify_mail_collectors import CollectorArtifactError, artifact_rows

    path = tmp_path / "_Investigate_InboxRules_user.json"
    path.write_text('{"id":1}\n{"id":2}')
    with pytest.raises(CollectorArtifactError, match="hawk_json_append"):
        artifact_rows(path, "hawk_rules")
    path.write_text('{"id":1}\ninvalid')
    with pytest.raises(json.JSONDecodeError):
        artifact_rows(path, "hawk_rules")


def test_trace_loss_classification_does_not_accept_other_wrong_values():
    from tools.qualify_mail_collectors import CollectorArtifactError, verify_rows

    wanted = [{"id": "a", "Received": "2026-10-07T00:00:00.123456+00:00"}]
    actual = [{"id": "a", "Received": "2026-10-07T00:00:00+00:00"}]
    with pytest.raises(CollectorArtifactError, match="mes_csv_timestamp_precision"):
        verify_rows(actual, wanted, "id", collector="mes_trace")
    actual[0]["Received"] = "2026-10-07T01:00:00+00:00"
    with pytest.raises(AssertionError) as info:
        verify_rows(actual, wanted, "id", collector="mes_trace")
    assert not isinstance(info.value, CollectorArtifactError)


def test_runtime_cannot_pair_legacy_metadata_with_new_volume(tmp_path):
    from tools.release_storage import validate_transition

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    validate_transition(runtime, "project", None)
    (runtime / "provisioning.json").write_text("{}")
    with pytest.raises(RuntimeError, match="fresh-runtime"):
        validate_transition(runtime, "project", None)
    volume = {
        "Labels": {
            "com.docker.compose.project": "project",
            "org.mockazure.purpose": "release-postgresql",
        }
    }
    with pytest.raises(RuntimeError):
        validate_transition(runtime, "project", volume)
    (runtime / "instance").mkdir()
    (runtime / "instance/native.json").write_text("{}")
    validate_transition(runtime, "project", volume)
    (runtime / "postgres").mkdir()
    with pytest.raises(RuntimeError, match="fresh-runtime"):
        validate_transition(runtime, "project", volume)


def test_run_evidence_hashes_detect_lost_or_changed_baseline(tmp_path):
    import hashlib

    from tools.qualify_mail_collectors import PROFILE, validate_collector_hashes

    def put(name, content):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    run = "work/mail-collector-runs/original-1/"
    result = {
        "profile": "original",
        "source_hashes": {"source.ps1": put("work/vendor/source.ps1", "source")},
        "output_hashes": {"result.csv": put(PROFILE.text(".runtime/m4") + "/result.csv", "id")},
        "run_hashes": {
            run + "evidence.json": put(run + "evidence.json", '{"profile":"original"}'),
            run + "process.log": put(run + "process.log", "original failure"),
        },
    }
    validate_collector_hashes({"hawk_rules": result}, tmp_path)
    put(run + "process.log", "overwritten patched log")
    with pytest.raises(AssertionError, match="Run evidence changed"):
        validate_collector_hashes({"hawk_rules": result}, tmp_path)

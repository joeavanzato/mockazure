import hashlib
import importlib.util
import json
import zipfile

import pytest


def tool():
    spec = importlib.util.spec_from_file_location("prepare_clients", "tools/prepare_clients.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(tmp_path):
    artifact = tmp_path / "client.zip"
    with zipfile.ZipFile(artifact, "w") as bundle:
        bundle.writestr("Connect.ps1", "function Connect-Original { 'original' }")
    manifest = {
        "schema_version": 1,
        "artifacts": [
            {
                "name": "client",
                "archive": "client.zip",
                "extracted": "modules/client/1",
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                "source": "https://unreachable.invalid/client.zip",
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    tool().prepare(path, tmp_path)
    return path, artifact


def test_offline_verification_rejects_tampered_archive(tmp_path):
    path, artifact = fixture(tmp_path)
    tool().prepare(path, tmp_path, verify_only=True)
    artifact.write_bytes(b"changed-client")
    with pytest.raises(ValueError, match="SHA256"):
        tool().prepare(path, tmp_path, verify_only=True)


@pytest.mark.parametrize("change", ["modified", "missing", "unexpected", "sibling-version"])
def test_offline_verification_checks_executed_tree(tmp_path, change):
    path, _ = fixture(tmp_path)
    extracted = tmp_path / "modules/client/1"
    if change == "modified":
        (extracted / "Connect.ps1").write_text("function Connect-Original { 'replacement' }")
    elif change == "missing":
        (extracted / "Connect.ps1").unlink()
    elif change == "unexpected":
        (extracted / "Extra.psm1").write_text("'injected'")
    else:
        sibling = tmp_path / "modules/client/999"
        sibling.mkdir()
        (sibling / "client.psm1").write_text("'injected newer version'")
    with pytest.raises(ValueError, match="Extracted client"):
        tool().prepare(path, tmp_path, verify_only=True)


def test_archive_paths_cannot_escape_output(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifacts": [
                    {
                        "archive": "../outside.zip",
                        "sha256": "0" * 64,
                        "source": "https://unreachable.invalid",
                    }
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="outside"):
        tool().prepare(path, tmp_path, verify_only=True)


def test_evidence_module_pins_are_required():
    from pathlib import Path

    manifest = json.loads(Path("integrations/manifest.json").read_text())
    artifacts = {item["name"]: item for item in manifest["artifacts"]}
    for name in (
        "Microsoft.Graph.Reports",
        "Microsoft.Graph.Identity.SignIns",
        "Microsoft.Graph.Users.Actions",
    ):
        assert artifacts[name]["version"] == "2.35.1"
        assert len(artifacts[name]["sha256"]) == 64
        assert artifacts[name]["probe"] == "integrations/contracts/graph-evidence.md"
    packages = {item["name"]: item["version"] for item in manifest["python"]["packages"]}
    assert packages["azure-identity"] == "1.26.0"


def test_dependency_mismatch_rejected_before_acquisition(tmp_path):
    path, _ = fixture(tmp_path)
    value = json.loads(path.read_text())
    value["artifacts"][0]["dependencies"] = ["Missing.Module==2.35.1"]
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="dependency"):
        tool().prepare(path, tmp_path, verify_only=True)

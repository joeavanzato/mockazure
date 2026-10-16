import hashlib
import importlib.util
import json

import pytest


def tool():
    spec = importlib.util.spec_from_file_location("prepare_clients", "tools/prepare_clients.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_offline_verification_rejects_tampered_archive(tmp_path):
    artifact = tmp_path / "client.zip"
    artifact.write_bytes(b"pinned-client")
    manifest = {
        "schema_version": 1,
        "artifacts": [
            {
                "name": "client",
                "archive": "client.zip",
                "sha256": hashlib.sha256(b"pinned-client").hexdigest(),
                "source": "https://unreachable.invalid/client.zip",
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    tool().prepare(path, tmp_path, verify_only=True)
    artifact.write_bytes(b"changed-client")
    with pytest.raises(ValueError, match="SHA256"):
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

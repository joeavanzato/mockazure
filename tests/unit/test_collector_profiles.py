import hashlib
import json
import zipfile

import pytest

from tools.collector_profiles import prepare_profile, validate_profile


@pytest.fixture
def project(tmp_path):
    vendor = tmp_path / "work/vendor"
    vendor.mkdir(parents=True)
    archive = vendor / "collector.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("source.ps1", "original\n")
        bundle.writestr("LICENSE", "license retained\n")
    extracted = vendor / "collectors/example"
    extracted.mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    integrations = tmp_path / "integrations"
    (integrations / "collector-patches").mkdir(parents=True)
    (integrations / "manifest.json").write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "archive": "collector.zip",
                        "extracted": "collectors/example",
                        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    }
                ]
            }
        )
    )
    patch = {
        "schema_version": 1,
        "profile": "serialization-v1",
        "patches": [
            {
                "path": "collectors/example/source.ps1",
                "before": "original",
                "after": "patched",
                "before_sha256": hashlib.sha256(b"original\n").hexdigest(),
                "after_sha256": hashlib.sha256(b"patched\n").hexdigest(),
            }
        ],
    }
    (integrations / "collector-patches/serialization-v1.json").write_text(json.dumps(patch))
    return tmp_path


def test_profile_preserves_original_and_licenses(project):
    target = prepare_profile(project, "serialization-v1")
    assert (target / "collectors/example/source.ps1").read_text() == "patched\n"
    assert (target / "collectors/example/LICENSE").read_text() == "license retained\n"
    assert (project / "work/vendor/collectors/example/source.ps1").read_text() == "original\n"
    assert validate_profile(project, "serialization-v1") == target


@pytest.mark.parametrize("mutation", ["extra", "changed", "missing"])
def test_existing_profile_is_never_silently_repaired(project, mutation):
    target = prepare_profile(project, "serialization-v1")
    path = target / "collectors/example/source.ps1"
    if mutation == "extra":
        (target / "extra.ps1").write_text("injected")
    elif mutation == "changed":
        path.write_text("changed")
    else:
        path.unlink()
    for check in (validate_profile, prepare_profile):
        with pytest.raises(ValueError):
            check(project, "serialization-v1")


@pytest.mark.parametrize(
    "field,value",
    [
        ("path", "../escape"),
        ("before", "absent"),
        ("before_sha256", "0" * 64),
        ("after_sha256", "0" * 64),
    ],
)
def test_patch_is_bound_to_exact_source_and_result(project, field, value):
    path = project / "integrations/collector-patches/serialization-v1.json"
    data = json.loads(path.read_text())
    data["patches"][0][field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        prepare_profile(project, "serialization-v1")
    assert not (project / "work/collector-profiles/serialization-v1").exists()


def test_modified_upstream_rejected(project):
    (project / "work/vendor/collectors/example/source.ps1").write_text("modified")
    with pytest.raises(ValueError):
        prepare_profile(project, "serialization-v1")


def test_archive_tampering_rejected(project):
    (project / "work/vendor/collector.zip").write_bytes(b"bad")
    with pytest.raises(ValueError):
        prepare_profile(project, "serialization-v1")


def test_unknown_profile_rejected(project):
    with pytest.raises(ValueError):
        prepare_profile(project, "../original")

"""Candidate archives are exact tracked source, not a workspace snapshot."""

import io
import subprocess
import tarfile
import zipfile

import pytest

PACKAGE = {
    "mockazure/__init__.py": b'__version__ = "0.1.0.dev0"\n',
    "mockazure/resources/schema.json": b'{"types":{}}\n',
}
META = "mockazure-0.1.0.dev0.dist-info/"
MEMBERS = {
    **PACKAGE,
    META + "METADATA": b"Name: mockazure\n",
    META + "WHEEL": b"Wheel-Version: 1.0\n",
    META + "RECORD": b"",
    META + "licenses/LICENSE": b"Apache-2.0\n",
    META + "licenses/NOTICE": b"MockAzure\n",
}


def wheel(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in members:
            archive.writestr(name, body)
    return path


def test_exact_package_bytes_and_required_licenses(tmp_path):
    from tools.build_release import inspect_wheel

    path = wheel(tmp_path / "candidate.whl", MEMBERS.items())
    assert inspect_wheel(path, PACKAGE)["package_files"] == len(PACKAGE)


@pytest.mark.parametrize(
    "name",
    [
        "../escape.py",
        "/absolute.py",
        "C:/escape.py",
        "mockazure\\escape.py",
        "mockazure/.env",
        "mockazure/secret.pem",
        "mockazure/tenant.db",
    ],
)
def test_reject_unsafe_or_unexpected_wheel_members(tmp_path, name):
    from tools.build_release import inspect_wheel

    path = wheel(tmp_path / "candidate.whl", [*MEMBERS.items(), (name, b"unwanted")])
    with pytest.raises(ValueError):
        inspect_wheel(path, PACKAGE)


def test_duplicate_case_alias_and_missing_license(tmp_path):
    from tools.build_release import inspect_wheel

    for extra in ("mockazure/__init__.py", "MOCKAZURE/__init__.py"):
        with (
            pytest.warns(UserWarning)
            if extra in MEMBERS
            else __import__("contextlib").nullcontext()
        ):
            path = wheel(tmp_path / "candidate.whl", [*MEMBERS.items(), (extra, b"alias")])
        with pytest.raises(ValueError):
            inspect_wheel(path, PACKAGE)
    path = wheel(
        tmp_path / "candidate.whl",
        [(k, v) for k, v in MEMBERS.items() if not k.endswith("/NOTICE")],
    )
    with pytest.raises(ValueError):
        inspect_wheel(path, PACKAGE)


def test_changed_resource_and_private_key_never_pass(tmp_path):
    from tools.build_release import inspect_wheel

    changed = dict(MEMBERS)
    changed["mockazure/resources/schema.json"] = b"changed"
    with pytest.raises(ValueError):
        inspect_wheel(wheel(tmp_path / "candidate.whl", changed.items()), PACKAGE)
    changed["mockazure/resources/schema.json"] = b"-----BEGIN PRIVATE KEY-----\nAAA\n"
    with pytest.raises(ValueError):
        inspect_wheel(
            wheel(tmp_path / "candidate.whl", changed.items()),
            {
                **PACKAGE,
                "mockazure/resources/schema.json": changed["mockazure/resources/schema.json"],
            },
        )


def test_tar_symlink_or_traversal_never_extracts(tmp_path):
    from tools.build_release import inspect_sdist

    for name, kind in (
        ("candidate/link", tarfile.SYMTYPE),
        ("candidate/../escape", tarfile.REGTYPE),
    ):
        path = tmp_path / "candidate.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            item = tarfile.TarInfo(name)
            item.type, item.linkname = kind, "../../escape"
            archive.addfile(item, io.BytesIO(b""))
        with pytest.raises(ValueError):
            inspect_sdist(path, {"README.md": b"readme"})


def test_source_export_uses_git_objects_excluding_untracked_and_runtime(tmp_path):
    from tools.build_release import export_source

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    git("init", "-q")
    for name, body in {
        "pyproject.toml": b"[project]\n",
        "README.md": b"readme",
        "LICENSE": b"Apache",
        "NOTICE": b"notice",
        "uv.lock": b"lock",
        "build-requirements.txt": b"build==1.6.1\n",
        "Dockerfile": b"FROM scratch\n",
        "src/mockazure/__init__.py": b"canonical\n",
    }.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    git("add", ".")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@mockazure.test",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "fixture",
    )
    (repo / ".env").write_text("private")
    (repo / "src/mockazure/untracked.py").write_text("do not copy")
    (repo / "src/mockazure/__init__.py").write_text("dirty workspace")
    destination = tmp_path / "export"
    result = export_source(repo, git("rev-parse", "HEAD"), destination)
    assert (destination / "src/mockazure/__init__.py").read_bytes() == b"canonical\n"
    assert not (destination / ".env").exists()
    assert not (destination / "src/mockazure/untracked.py").exists()
    assert result["src/mockazure/__init__.py"] == b"canonical\n"


def test_candidate_report_rejects_stale_or_missing_artifacts(tmp_path):
    from tools.build_release import validate_artifact_report

    with pytest.raises((ValueError, AssertionError)):
        validate_artifact_report({"passed": True, "source_fingerprint": "old"}, tmp_path, "new")


def test_build_tool_lock_participates_in_candidate_fingerprint(tmp_path):
    from tools.release_report import source_fingerprint

    for name in ("pyproject.toml", "uv.lock", "Dockerfile", "README.md", "LICENSE", "NOTICE"):
        (tmp_path / name).write_text("fixture")
    lock = tmp_path / "build-requirements.txt"
    lock.write_text("hatchling==1.32.4")
    first = source_fingerprint(tmp_path)
    lock.write_text("changed build tool bytes")
    assert source_fingerprint(tmp_path) != first


@pytest.mark.parametrize(
    "changed", ["status", "parity", "package_hashes", "probes", "source_bytes"]
)
def test_forged_completion_and_parity_are_rejected(tmp_path, monkeypatch, changed):
    import hashlib

    from tools import build_release as builder

    source = {"src/" + name: body for name, body in PACKAGE.items()}
    source.update({"README.md": b"readme", "LICENSE": b"Apache", "NOTICE": b"notice"})
    monkeypatch.setattr(builder, "source_objects", lambda *args: source)
    monkeypatch.setattr(builder, "git", lambda *args: b"b" * 40)
    for name, contents in source.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
    hashes = {name: hashlib.sha256(body).hexdigest() for name, body in PACKAGE.items()}
    values = {
        "schema_version": 1,
        "status": "complete",
        "passed": True,
        "reproducible": True,
        "parity": True,
        "source_fingerprint": "a" * 64,
        "source_commit": "b" * 40,
        "source_hashes": {name: hashlib.sha256(body).hexdigest() for name, body in source.items()},
        "package_hashes": hashes,
        "sdist_hashes": {name: hashlib.sha256(body).hexdigest() for name, body in source.items()},
        "probes": {
            name: {"package": hashes, "migration_head": "037", "capabilities": {}}
            for name in ("native", "docker")
        },
        "artifacts": {"wheel": [], "sdist": []},
    }
    root = tmp_path / "work/release"
    root.mkdir(parents=True)
    for index in (1, 2):
        path = wheel(root / f"candidate-{index}.whl", MEMBERS.items())
        values["artifacts"]["wheel"].append(
            {
                "path": path.relative_to(tmp_path).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
        path = root / f"candidate-{index}.tar.gz"
        # Use fixed gzip metadata by copying the first actual archive for fixture equality.
        if index == 1:
            with tarfile.open(path, "w:gz") as archive:
                for name, body in {**source, "PKG-INFO": b"Name: mockazure"}.items():
                    member = tarfile.TarInfo("mockazure-0.1.0.dev0/" + name)
                    member.size = len(body)
                    archive.addfile(member, io.BytesIO(body))
        else:
            path.write_bytes((root / "candidate-1.tar.gz").read_bytes())
        values["artifacts"]["sdist"].append(
            {
                "path": path.relative_to(tmp_path).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    assert builder.validate_artifact_report(values, tmp_path, "a" * 64)
    if changed == "source_bytes":
        (tmp_path / "src/mockazure/__init__.py").write_bytes(b"changed shipped bytes")
    elif changed == "status":
        values[changed] = "running"
    elif changed == "parity":
        values[changed] = 1
    elif changed == "package_hashes":
        values[changed] = {}
    else:
        values["probes"]["docker"]["migration_head"] = "old"
    with pytest.raises((AssertionError, ValueError)):
        builder.validate_artifact_report(values, tmp_path, "a" * 64)


def test_candidate_probe_uses_current_capability_request_contract():
    import json
    import sys

    from tools.build_release import PROBE

    result = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["migration_head"] == "064"
    assert value["capabilities"]["authentication_policy_profile"]["new_tenants_enforced"] is True

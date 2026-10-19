"""Build and verify a candidate from committed, allowlisted Git object bytes."""

import hashlib
import json
import re
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = frozenset(
    {
        "pyproject.toml",
        "uv.lock",
        "build-requirements.txt",
        "Dockerfile",
        "README.md",
        "LICENSE",
        "NOTICE",
    }
)
SDIST_ROOTS = SOURCE_ROOTS - {"uv.lock", "Dockerfile"}
LIMIT = 256 * 1024 * 1024


def sha(value):
    return hashlib.sha256(value).hexdigest()


def safe_member(name):
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or ":" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/"))
        or any(ord(char) < 32 for char in name)
    ):
        raise ValueError("Unsafe archive member")
    if path.name.startswith(".env") or path.suffix.lower() in {
        ".pem",
        ".key",
        ".pfx",
        ".p12",
        ".db",
        ".sqlite",
        ".env",
    }:
        raise ValueError("Private/runtime file is not a candidate member")
    return path


def public_bytes(value):
    if re.search(rb"(?m)^-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----\r?$", value):
        raise ValueError("Private key material is not a candidate member")


def expected_hash(value):
    return sha(value) if isinstance(value, bytes) else value


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], timeout=120)


def source_objects(root, commit):
    if not re.fullmatch("[0-9a-f]{40}", commit):
        raise ValueError("An exact source commit is required")
    records = git(root, "ls-tree", "-rz", "--full-tree", commit).split(b"\0")
    selected = []
    for record in records:
        if not record:
            continue
        metadata, encoded = record.split(b"\t", 1)
        name = encoded.decode("utf-8")
        if name not in SOURCE_ROOTS and not name.startswith("src/mockazure/"):
            continue
        safe_member(name)
        mode, kind, identifier = metadata.decode().split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("Only tracked regular files may enter a candidate")
        selected.append((name, identifier))
    if not SOURCE_ROOTS <= {name for name, _ in selected}:
        raise ValueError("Required source/build/licence files are missing")
    process = subprocess.run(
        ["git", "-C", str(root), "cat-file", "--batch"],
        input="".join(identifier + "\n" for _, identifier in selected).encode(),
        capture_output=True,
        check=True,
        timeout=120,
    )
    result, offset = {}, 0
    for name, identifier in selected:
        end = process.stdout.index(b"\n", offset)
        oid, kind, size = process.stdout[offset:end].decode().split()
        if oid != identifier or kind != "blob":
            raise ValueError("Unexpected Git object response")
        size = int(size)
        value = process.stdout[end + 1 : end + 1 + size]
        if len(value) != size or size > LIMIT:
            raise ValueError("Invalid source object size")
        public_bytes(value)
        result[name] = value
        offset = end + 2 + size
    return result


def export_source(root, commit, destination):
    values = source_objects(root, commit)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    # Hatch searches ancestors for VCS exclusions and force-includes them in sdists.
    # An empty boundary prevents reading parent checkout files; it is not packaged.
    (destination / ".git").mkdir()
    for name, value in values.items():
        target = destination.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    return values


def archive_members(path):
    values, seen, total = {}, set(), 0
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for item in archive.infolist():
                safe_member(item.filename)
                canonical = item.filename.rstrip("/").casefold()
                if canonical in seen:
                    raise ValueError("Duplicate/case-aliased archive member")
                seen.add(canonical)
                if item.flag_bits & 1 or stat.S_IFMT(item.external_attr >> 16) not in {
                    0,
                    stat.S_IFREG,
                    stat.S_IFDIR,
                }:
                    raise ValueError("Non-regular or encrypted archive member")
                if item.is_dir():
                    continue
                total += item.file_size
                if total > LIMIT or len(seen) > 10000:
                    raise ValueError("Candidate archive exceeds bounds")
                value = archive.read(item)
                public_bytes(value)
                values[item.filename] = value
    else:
        with tarfile.open(path, "r:gz") as archive:
            for item in archive:
                safe_member(item.name)
                canonical = item.name.rstrip("/").casefold()
                if canonical in seen:
                    raise ValueError("Duplicate/case-aliased archive member")
                seen.add(canonical)
                if item.isdir():
                    continue
                if not item.isfile():
                    raise ValueError("Non-regular archive member")
                total += item.size
                if total > LIMIT or len(seen) > 10000:
                    raise ValueError("Candidate archive exceeds bounds")
                with archive.extractfile(item) as stream:
                    value = stream.read()
                public_bytes(value)
                values[item.name] = value
    return values


def inspect_wheel(path, expected_package):
    values = archive_members(path)
    package = {key: value for key, value in values.items() if key.startswith("mockazure/")}
    if set(package) != set(expected_package) or any(
        sha(value) != expected_hash(expected_package[key]) for key, value in package.items()
    ):
        raise ValueError("Wheel package differs from committed source")
    metadata = {key.split("/", 1)[0] for key in values if ".dist-info/" in key}
    if len(metadata) != 1 or not re.fullmatch(
        r"mockazure-[0-9A-Za-z.+]+\.dist-info", next(iter(metadata))
    ):
        raise ValueError("Unexpected distribution identity")
    prefix = next(iter(metadata)) + "/"
    required = {"METADATA", "WHEEL", "RECORD", "licenses/LICENSE", "licenses/NOTICE"}
    allowed = required | {"entry_points.txt"}
    if not {prefix + name for name in required} <= set(values):
        raise ValueError("Required metadata/licences missing")
    if set(values) - set(package) - {prefix + name for name in allowed}:
        raise ValueError("Unexpected wheel content")
    return {
        "sha256": sha(Path(path).read_bytes()),
        "package_files": len(package),
        "members": {name: sha(value) for name, value in values.items()},
    }


def inspect_sdist(path, expected_source):
    values = archive_members(path)
    roots = {name.split("/", 1)[0] for name in values}
    if len(roots) != 1 or not re.fullmatch(r"mockazure-[0-9A-Za-z.+]+", next(iter(roots))):
        raise ValueError("Unexpected source distribution identity")
    prefix = next(iter(roots)) + "/"
    source = {name[len(prefix) :]: value for name, value in values.items()}
    if set(source) != set(expected_source) | {"PKG-INFO"}:
        raise ValueError("Source archive member set differs from committed allowlist")
    if any(sha(source[name]) != expected_hash(value) for name, value in expected_source.items()):
        raise ValueError("Source archive bytes differ from committed source")
    return {
        "sha256": sha(Path(path).read_bytes()),
        "source_files": len(expected_source),
        "members": {name: sha(value) for name, value in values.items()},
    }


def validate_artifact_report(report, root, fingerprint):
    assert report["source_fingerprint"] == fingerprint
    assert all(report[key] is True for key in ("passed", "reproducible", "parity"))
    assert re.fullmatch("[0-9a-f]{40}", report["source_commit"])
    assert report["schema_version"] == 1 and report["status"] == "complete"
    root = Path(root).resolve()
    source = source_objects(root, report["source_commit"])
    current_commit = git(root, "rev-parse", "HEAD").decode().strip()
    assert source_objects(root, current_commit) == source
    # A saved fingerprint/commit cannot attest changed or missing current inputs.
    assert all((root / name).read_bytes() == contents for name, contents in source.items())
    assert report["source_hashes"] == {name: sha(value) for name, value in source.items()}
    package_hashes = {
        name.removeprefix("src/"): sha(value)
        for name, value in source.items()
        if name.startswith("src/mockazure/")
    }
    sdist_hashes = {
        name: sha(value)
        for name, value in source.items()
        if name in SDIST_ROOTS or name.startswith("src/mockazure/")
    }
    assert report["package_hashes"] == package_hashes
    assert report["sdist_hashes"] == sdist_hashes
    native, container = report["probes"]["native"], report["probes"]["docker"]
    assert native["package"] == container["package"] == package_hashes
    assert native["migration_head"] == container["migration_head"]
    assert native["capabilities"] == container["capabilities"]
    for kind, inspect, expected in (
        ("wheel", inspect_wheel, report["package_hashes"]),
        ("sdist", inspect_sdist, report["sdist_hashes"]),
    ):
        builds = report["artifacts"][kind]
        assert len(builds) == 2 and builds[0]["sha256"] == builds[1]["sha256"]
        assert builds[0]["path"] != builds[1]["path"]
        for build in builds:
            path = (root / build["path"]).resolve()
            assert path.is_relative_to((root / "work/release").resolve()) and path.is_file()
            assert inspect(path, expected)["sha256"] == build["sha256"]
    return True


PROBE = """
import asyncio, hashlib, importlib.metadata, json
from pathlib import Path
import mockazure
from alembic.script import ScriptDirectory
from mockazure.db.migrations import configuration
from mockazure.admin.routes import capabilities
from mockazure.config import Settings
from fastapi import FastAPI, Request
app = FastAPI()
app.state.settings = Settings(
    data_dir=Path("work/package-probe"), database_url="postgresql+psycopg://localhost/mockazure"
)
request = Request({"type": "http", "app": app})
root = Path(mockazure.__file__).parent
package = {
    "mockazure/" + p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts
}
from mockazure.cloud.contracts import verify_references
services = ("exchange", "arm-providers", "storage", "keyvault", "graph-workflows", "queries")
for service in services:
    verify_references(service)
print(json.dumps({
    "referencesVerified": list(services),
    "package": package,
    "migration_head": ScriptDirectory.from_config(configuration()).get_current_head(),
    "capabilities": asyncio.run(capabilities(request)),
    "dependencies": sorted(
        [{"name": d.metadata["Name"], "version": d.version}
         for d in importlib.metadata.distributions()], key=lambda d:d["name"].lower()),
}))
"""


def build_candidate(source_commit, output_dir):
    import os
    import sys
    from datetime import UTC, datetime

    from tools.release_report import source_fingerprint
    from tools.verify_release import UV, capture, run, save

    output_dir = Path(output_dir).resolve()
    assert output_dir.is_relative_to((ROOT / "work/release").resolve())
    assert not output_dir.exists(), "Retain prior builds; choose a fresh output directory"
    assert source_commit == git(ROOT, "rev-parse", "HEAD").decode().strip()
    if subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", "HEAD"], check=False).returncode:
        raise ValueError("Commit tracked changes before building the candidate")
    output_dir.mkdir(parents=True)
    report = {
        "schema_version": 1,
        "source_commit": source_commit,
        "source_fingerprint": source_fingerprint(),
        "created_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "passed": False,
        "reproducible": False,
        "parity": False,
        "release_ready": False,
        "release_note": "Candidate artifacts do not waive any mandatory qualification gate.",
    }
    report_path = output_dir / "manifest.json"
    save(report_path, report)
    pointer = ROOT / "work/release/artifacts.json"
    save(pointer, report)
    try:
        source = source_objects(ROOT, source_commit)
        # Docker and every qualification harness read the working tree. Require byte
        # parity with the exact Git inputs instead of hiding CRLF differences.
        if any((ROOT / name).read_bytes() != value for name, value in source.items()):
            raise ValueError("Normalize the owned clean worktree to committed source bytes first")
        report["source_hashes"] = {name: sha(value) for name, value in source.items()}
        package = {
            name.removeprefix("src/"): value
            for name, value in source.items()
            if name.startswith("src/mockazure/")
        }
        sdist_source = {
            name: value
            for name, value in source.items()
            if name in SDIST_ROOTS or name.startswith("src/mockazure/")
        }
        report["package_hashes"] = {name: sha(value) for name, value in package.items()}
        report["sdist_hashes"] = {name: sha(value) for name, value in sdist_source.items()}
        epoch = int(git(ROOT, "show", "-s", "--format=%ct", source_commit).decode().strip())
        report["source_date_epoch"] = epoch
        os.environ["SOURCE_DATE_EPOCH"] = str(epoch)
        os.environ["PYTHONHASHSEED"] = "0"
        build_id = source_commit[:12] + "-" + sha(str(output_dir).encode())[:8]
        environment = ROOT / ".runtime" / ("release-builder-" + build_id)
        assert not environment.exists(), "Use a fresh committed build environment"
        run([UV, "venv", "--python", sys.executable, environment], "candidate-builder-env")
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run(
            [
                UV,
                "pip",
                "sync",
                "--python",
                python,
                "--require-hashes",
                ROOT / "build-requirements.txt",
            ],
            "candidate-build-dependencies",
        )
        report["build_environment"] = json.loads(
            capture(
                [
                    python,
                    "-c",
                    "import importlib.metadata as m,json; print(json.dumps(sorted("
                    "[(d.metadata['Name'],d.version) for d in m.distributions()])))",
                ]
            )
        )
        report["build_lock_sha256"] = sha(source["build-requirements.txt"])
        report["artifacts"] = {"wheel": [], "sdist": []}
        for number in (1, 2):
            folder = output_dir / f"build-{number}"
            exported = folder / "source"
            export_source(ROOT, source_commit, exported)
            distribution = folder / "dist"
            run(
                [
                    python,
                    "-m",
                    "build",
                    "--no-isolation",
                    "--wheel",
                    "--sdist",
                    "--outdir",
                    distribution,
                    exported,
                ],
                f"candidate-build-{number}",
            )
            wheels, sdists = list(distribution.glob("*.whl")), list(distribution.glob("*.tar.gz"))
            assert len(wheels) == len(sdists) == 1
            for kind, artifact, inspector, expected in (
                ("wheel", wheels[0], inspect_wheel, package),
                ("sdist", sdists[0], inspect_sdist, sdist_source),
            ):
                info = inspector(artifact, expected)
                info["path"] = artifact.relative_to(ROOT).as_posix()
                report["artifacts"][kind].append(info)
            save(report_path, report)
        assert all(
            entries[0]["sha256"] == entries[1]["sha256"] for entries in report["artifacts"].values()
        )
        report["reproducible"] = True
        save(report_path, report)

        installed = ROOT / ".runtime" / ("release-installed-" + build_id)
        assert not installed.exists()
        run([UV, "venv", "--python", sys.executable, installed], "candidate-install-env")
        installed_python = installed / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        console = installed / ("Scripts/mockazure.exe" if os.name == "nt" else "bin/mockazure")
        requirements = output_dir / "runtime-requirements.txt"
        run(
            [
                UV,
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                "--output-file",
                requirements,
            ],
            "candidate-runtime-lock",
        )
        run(
            [
                UV,
                "pip",
                "install",
                "--python",
                installed_python,
                "--require-hashes",
                "-r",
                requirements,
            ],
            "candidate-runtime-dependencies",
        )
        wheel = ROOT / report["artifacts"]["wheel"][0]["path"]
        run(
            [UV, "pip", "install", "--python", installed_python, "--no-deps", wheel],
            "candidate-wheel-install",
        )
        run([installed_python, "-m", "mockazure", "--help"], "candidate-module-cli")
        run([console, "--help"], "candidate-console-cli")
        native = json.loads(capture([installed_python, "-c", PROBE]))
        assert native["package"] == report["package_hashes"]
        image = "mockazure:m6-candidate-" + source_commit[:12]
        run(
            [
                "docker",
                "build",
                "--build-arg",
                "SOURCE_DATE_EPOCH=" + str(epoch),
                "--build-arg",
                "MOCKAZURE_SOURCE_FINGERPRINT=" + report["source_fingerprint"],
                "-t",
                image,
                output_dir / "build-1/source",
            ],
            "candidate-docker-build",
        )
        container = json.loads(
            capture(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--entrypoint",
                    "python",
                    image,
                    "-c",
                    PROBE,
                ]
            )
        )
        assert container["package"] == report["package_hashes"]
        assert native["migration_head"] == container["migration_head"]
        assert native["capabilities"] == container["capabilities"]
        run(
            ["docker", "run", "--rm", "--network", "none", image, "--help"],
            "candidate-container-cli",
        )
        # The five original-client suites remain separate mandatory gates.
        report["probes"] = {"native": native, "docker": container}
        report["image"] = {
            "name": image,
            "id": json.loads(capture(["docker", "image", "inspect", image]))[0]["Id"],
            "byte_reproducible_layers_claimed": False,
        }
        report.update(
            parity=True, passed=True, status="complete", completed_at=datetime.now(UTC).isoformat()
        )
        validate_artifact_report(report, ROOT, source_fingerprint())
        save(report_path, report)
        save(pointer, report)
        return report
    except BaseException as error:
        report.update(passed=False, status="failed", error_type=type(error).__name__)
        save(report_path, report)
        save(pointer, report)
        raise


def validate_cloud_distribution(report, commit, fingerprint):
    from tools.check_cloud_coverage import SERVICES
    from tools.verify_cloud_distribution import CASES, validate_junit

    try:
        assert report["passed"] is True
        assert report["sourceCommit"] == commit and report["sourceFingerprint"] == fingerprint
        assert set(report["distributions"]) == {"wheel", "sdist", "docker"}
        artifact = json.loads((ROOT / "work/release/artifacts.json").read_text(encoding="utf-8"))
        from tools.release_report import source_fingerprint

        validate_artifact_report(artifact, ROOT, source_fingerprint())
        assert artifact["source_commit"] == commit
        assert report["artifactManifestSha256"] == sha(
            (ROOT / "work/release/artifacts.json").read_bytes()
        )
        for result in report["distributions"].values():
            assert result["passed"] is True and result["caseNames"] == list(CASES)
            assert result["failed"] == result["skipped"] == 0 and result["passedTests"] >= 6
            assert result["probe"]["package"] == artifact["package_hashes"]
            assert set(result["probe"]["referencesVerified"]) == set(SERVICES)
            xml = (ROOT / result["junitPath"]).resolve()
            assert xml.is_relative_to(ROOT / "work/release") and xml.is_file()
            assert sha(xml.read_bytes()) == result["junitSha256"]
            assert result["passedTests"] == validate_junit(xml)
    except (AssertionError, KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "Cloud distribution qualification is missing, stale or incomplete"
        ) from error
    return True


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_candidate(args.commit, args.output)
    print(json.dumps({key: result[key] for key in ("status", "passed", "reproducible", "parity")}))
    print("Candidate built; release acceptance still requires every mandatory gate.")


if __name__ == "__main__":
    main()

"""Run existing qualification suites in fresh owned release profiles; retain every failure."""

import argparse
import hashlib
import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.qualification_profile import STAGES, profile_for
from tools.release_report import failed_cases, release_readiness, source_fingerprint

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "work/release"
UV = shutil.which("uv") or str(Path.home() / ".local/bin/uv.exe")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def run(arguments, label, *, timeout=1200, check=True):
    LOGS.mkdir(parents=True, exist_ok=True)
    print(label, flush=True)
    with (LOGS / (label + ".log")).open("w", encoding="utf-8") as stream:
        result = subprocess.run(
            [str(item) for item in arguments],
            cwd=ROOT,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=os.environ.copy(),
        )
    if check and result.returncode:
        raise RuntimeError(f"{label} failed; inspect the project-local log")
    return result.returncode


def capture(arguments):
    result = subprocess.run(
        [str(item) for item in arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=True,
    )
    return result.stdout.strip()


def reuse_public_archives(source):
    """Copy exactly named, hash-verified public archives; never copy extracted state."""
    from tools.prepare_clients import beneath, prepare

    source, destination = source.resolve(), ROOT / "work/vendor"
    manifest = ROOT / "integrations/manifest.json"
    for item in json.loads(manifest.read_text(encoding="utf-8"))["artifacts"]:
        original = beneath(source, item["archive"])
        assert original.is_file() and not original.is_symlink() and not original.is_junction()
        contents = original.read_bytes()
        assert hashlib.sha256(contents).hexdigest() == item["sha256"]
        target = beneath(destination, item["archive"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            assert hashlib.sha256(target.read_bytes()).hexdigest() == item["sha256"]
        else:
            target.write_bytes(contents)
    prepare(manifest, destination)
    prepare(manifest, destination, verify_only=True)


def ensure_network(profile):
    name = profile.project + "-clients-isolated"
    existing = subprocess.run(
        ["docker", "network", "inspect", name],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    owner = str((ROOT / profile.runtime_dir).resolve())
    if existing.returncode:
        run(
            [
                "docker",
                "network",
                "create",
                "--internal",
                "--subnet",
                profile.internal_subnet,
                "--label",
                "org.mockazure.runtime-root=" + owner,
                name,
            ],
            profile.stage + "-network",
        )
    value = json.loads(capture(["docker", "network", "inspect", name]))[0]
    assert value["Internal"] is True
    assert value["Labels"].get("org.mockazure.runtime-root") == owner
    assert [entry["Subnet"] for entry in value["IPAM"]["Config"]] == [profile.internal_subnet]


def prepare_suite(stage):
    profile = profile_for(stage)
    assert profile.release
    profile.require_local_origin(f"https://localhost:{profile.https_port}")
    if not (ROOT / profile.runtime_dir / "instance/native.json").exists():
        for port in profile.ports:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
    ensure_network(profile)
    surface = profile.surface
    module = importlib.import_module("tools.verify_" + surface)
    assert module.PROFILE.release and module.PROFILE == profile
    initializer = [sys.executable, "-m", "tools.prepare_" + surface]
    if stage not in {"m3", "m3b"}:
        initializer += ["initialize"]
    run(initializer, stage + "-initialize")
    fingerprint = module.source_fingerprint()
    run(
        [
            "docker",
            "build",
            "--build-arg",
            "MOCKAZURE_SOURCE_FINGERPRINT=" + fingerprint,
            "-t",
            profile.image,
            ".",
        ],
        stage + "-image",
    )
    run(
        ["docker", "tag", "mockazure:m6-python-clients", profile.image + "-python-clients"],
        stage + "-client-image",
    )
    run(["docker", "compose", "-f", profile.compose, "up", "-d"], stage + "-up")
    provision = [sys.executable, "-m", "tools.prepare_" + surface]
    provision += ["--provision"] if stage in {"m3", "m3b"} else ["provision"]
    run(provision, stage + "-provision", timeout=1800)
    if stage in {"m4", "m5"}:
        run(
            [sys.executable, "-m", "tools.prepare_" + surface + "_evidence"],
            stage + "-evidence",
            timeout=1200,
        )


def validate_graph(value, module):
    required = {
        "snapshot_5000_concurrent_mutation",
        "two_api_replicas",
        "api_restart",
        "postgres_restart",
        "continuation_replay",
        "delta_resume",
        "cross_tenant_reference_denied",
        "wrong_audience_denied",
        "shared_429_recovery",
        "clean_wheel_install",
        "native_cli_dataset",
        "native_network_blocked",
        "container_python_network_blocked",
        "container_powershell_network_blocked",
    }
    assert set(value["checks"]) == required
    assert all(flag is True for flag in value["checks"].values())
    assert set(value["clients"]) == {"native_python", "container_python", "container_powershell"}
    for result in value["clients"].values():
        assert result["passed"] is True and set(result["modes"]) == {"application", "delegated"}
        for workflow in result["modes"].values():
            assert workflow["seed_user_count"] == 5000
            assert workflow["seed_first_id"] == value["seed_first_id"]
            for key in (
                "create",
                "profile_update",
                "membership",
                "delta",
                "delete",
                "permission_denied",
            ):
                assert workflow[key] is True
    assert value["source_fingerprint"] == module.source_fingerprint()
    assert value["manifest_sha256"] == module.sha(ROOT / "integrations/manifest.json")
    assert value["lock_sha256"] == module.sha(ROOT / "uv.lock")
    assert value["wheel_sha256"] == module.sha(next((ROOT / "dist").glob("mockazure-*.whl")))


def validate_suite(stage, path):
    value = json.loads(path.read_text(encoding="utf-8"))
    module = importlib.import_module("tools.verify_" + profile_for(stage).surface)
    assert module.PROFILE.release
    age = datetime.now(UTC) - datetime.fromisoformat(value["qualified_at"])
    assert timedelta(0) <= age < timedelta(days=1)
    assert value["source_fingerprint"] == module.source_fingerprint()
    # Even failed original M4 outputs must have intact artifact/source provenance.
    if stage == "m4":
        from tools.qualify_mail_collectors import validate_collector_hashes

        validate_collector_hashes(value["collectors"], ROOT)
    if stage == "m3":
        validate_graph(value, module)
    else:
        module.validate_report(value, root=ROOT)
    return value


def validate_release(report):
    """Revalidate actual stage artifacts before interpreting saved readiness flags."""
    for stage in STAGES:
        entry = report.get("suites", {}).get(stage, {})
        if entry.get("status") != "passed":
            continue
        try:
            path = (ROOT / entry["report"]).resolve()
            assert path.is_relative_to(LOGS.resolve())
            assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["report_sha256"]
            validate_suite(stage, path)
        except (AssertionError, KeyError, ValueError, OSError):
            entry.update(status="failed", validated=False)
    return release_readiness(report, source_fingerprint())


def run_suites(output, stages):
    assert os.environ.get("MOCKAZURE_QUALIFICATION_PROFILE") == "release"
    fingerprint = source_fingerprint()
    if output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
    else:
        previous = {}
    report = (
        previous
        if previous.get("source_fingerprint") == fingerprint
        else {
            "schema_version": 1,
            "source_fingerprint": fingerprint,
            "source_commit": capture(["git", "rev-parse", "HEAD"]),
            "suites": {},
            "scale": {},
            "artifacts": {},
        }
    )
    report.update(qualified_at=datetime.now(UTC).isoformat(), release_ready=False)
    save(output, report)
    for stage in stages:
        profile = profile_for(stage)
        path = LOGS / (stage + "-report.json")
        entry = {
            "status": "running",
            "validated": False,
            "exit_code": None,
            "report": path.relative_to(ROOT).as_posix(),
            "failed_cases": [],
        }
        report["suites"][stage] = entry
        # Replace stale success before the first potentially failing setup operation.
        save(path, {"schema_version": 1, "passed": False, "status": "running"})
        save(output, report)
        try:
            prepare_suite(stage)
            code = run(
                [sys.executable, "tools/verify_" + profile.surface + ".py", "--report", path],
                stage + "-qualification",
                timeout=2400,
                check=False,
            )
            entry["exit_code"] = code
            raw = json.loads(path.read_text(encoding="utf-8"))
            entry["failed_cases"] = failed_cases(raw)
            validate_suite(stage, path)
            assert code == 0
            entry.update(status="passed", validated=True)
        except (
            AssertionError,
            KeyError,
            ValueError,
            OSError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as error:
            entry.update(status="failed", validated=False, error_type=type(error).__name__)
            print(
                f"{stage}: failed; retained evidence and continuing independent suites", flush=True
            )
        finally:
            if path.exists():
                entry["report_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            entry["finished_at"] = datetime.now(UTC).isoformat()
            # Only this explicit release compose project is stopped, never predecessor projects.
            run(["docker", "compose", "-f", profile.compose, "stop"], stage + "-stop", check=False)
            report["qualified_at"] = datetime.now(UTC).isoformat()
            report.update(validate_release(report))
            save(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stages", nargs="+", choices=list(STAGES), default=list(STAGES))
    parser.add_argument("--reuse-public-archives", type=Path)
    parser.add_argument("--report", type=Path, default=LOGS / "qualification.json")
    args = parser.parse_args()
    os.environ["MOCKAZURE_QUALIFICATION_PROFILE"] = "release"
    os.environ.setdefault("UV_CACHE_DIR", str(ROOT.parents[1] / ".runtime/uv-cache"))
    output = args.report.resolve()
    assert output.is_relative_to(LOGS.resolve())
    if args.reuse_public_archives:
        reuse_public_archives(args.reuse_public_archives)
    run([sys.executable, "tools/prepare_clients.py", "--verify"], "vendor-verify")
    run([UV, "build", "--wheel", "--out-dir", "dist"], "wheel")
    run(
        [
            "docker",
            "build",
            "-f",
            "integrations/Dockerfile.python",
            "-t",
            "mockazure:m6-python-clients",
            ".",
        ],
        "python-client-image",
    )
    report = run_suites(output, args.stages)
    print(
        json.dumps(
            {key: report[key] for key in ("qualification_ready", "release_ready", "failed_gates")}
        ),
        flush=True,
    )

    raise SystemExit(0 if report["release_ready"] else 2)


if __name__ == "__main__":
    main()

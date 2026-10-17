"""Strict source-, schema-, artifact- and runtime-bound M5 acceptance evidence."""

import hashlib
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.qualification_profile import profile_for  # noqa: E402

PROFILE = profile_for("m5")
RUNTIME_CHECKS = frozenset(
    {
        "clean_wheel_install",
        "runtime_package_bytes",
        "two_api_replicas",
        "two_workers",
        "api_restart",
        "postgres_restart",
        "worker_restart",
        "delete_pending_restart",
        "activity_snapshot_resume",
        "tenant_isolation",
        "scope_denied",
        "page_reauthorization",
        "native_evidence",
        "fault_retry",
        "native_network_blocked",
        "container_python_network_blocked",
        "container_powershell_network_blocked",
    }
)
CLIENT_CHECKS = frozenset(
    {
        "discovery",
        "inventory",
        "group_crud",
        "tag_crud",
        "role_definitions",
        "role_assignments",
        "activity_paging",
        "activity_filters",
        "permission_denied",
    }
)
PYTHON_VERSIONS = {
    "msal": "1.39.0",
    "azure-mgmt-resource": "23.4.0",
    "azure-mgmt-authorization": "4.0.0",
    "azure-mgmt-monitor": "6.0.2",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_fingerprint(root=ROOT):
    files = {
        root / name
        for name in (
            "README.md",
            "LICENSE",
            "NOTICE",
            "pyproject.toml",
            "uv.lock",
            "build-requirements.txt",
            "Dockerfile",
            "integrations/Dockerfile.python",
            PROFILE.text("compose.m5.yaml"),
            PROFILE.text("compose.clients.m5.yaml"),
            PROFILE.text("deploy/haproxy-m5.cfg"),
        )
    }
    from tools.source_inputs import qualification_files

    files.update(qualification_files(root))
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def validate_results(report):
    from tools.qualify_arm_collectors import validate_cases

    assert report["schema_version"] == 1 and report["passed"] is True
    age = datetime.now(UTC) - datetime.fromisoformat(report["qualified_at"])
    assert timedelta(0) <= age < timedelta(days=1)
    assert set(report["checks"]) == RUNTIME_CHECKS
    assert all(value is True for value in report["checks"].values())
    assert set(report["clients"]) == {"native_python", "container_python", "container_powershell"}
    for result in report["clients"].values():
        assert result["passed"] is True and set(result["modes"]) == {"application", "delegated"}
        for checks in result["modes"].values():
            assert set(checks) == CLIENT_CHECKS and all(value is True for value in checks.values())
    collector = report["collector"]
    assert collector["passed"] is True
    assert type(collector["records_verified"]) is int and collector["records_verified"] >= 1003
    assert collector["source_hashes"] and collector["output_hashes"]
    validate_cases(collector["cases"])


def validate_report(report, *, root=ROOT):
    from mockazure.providers.arm.contracts import verify_references
    from tools.qualify_arm_collectors import validate_hashes

    validate_results(report)
    verify_references()
    assert report["source_fingerprint"] == source_fingerprint(root)
    assert re.fullmatch("[0-9a-f]{40}", report["source_commit"])
    assert report["manifest_sha256"] == sha(root / "integrations/manifest.json")
    assert report["lock_sha256"] == sha(root / "uv.lock")
    assert report["arm_references_sha256"] == sha(
        root / "src/mockazure/providers/arm/data/references.json"
    )
    wheels = list((root / "dist").glob("mockazure-*.whl"))
    assert len(wheels) == 1 and report["wheel_sha256"] == sha(wheels[0])
    assert re.fullmatch("sha256:[0-9a-f]{64}", report["runtime_image_id"])
    assert set(report["client_image_ids"]) == {"python", "powershell"}
    assert all(
        re.fullmatch("sha256:[0-9a-f]{64}", value) for value in report["client_image_ids"].values()
    )
    for name in ("native_python", "container_python"):
        assert report["clients"][name]["versions"] == PYTHON_VERSIONS
    assert report["clients"]["container_powershell"]["versions"] == {
        "Az.Accounts": "4.0.2",
        "Az.Resources": "7.8.0",
    }
    validate_hashes(report["collector"]["source_hashes"], root / "work/vendor")
    validate_hashes(report["collector"]["output_hashes"], root / PROFILE.text(".runtime/m5"))


def main():
    import argparse
    import json
    import os
    import shutil
    import socket
    import ssl
    import sys
    import time
    import zipfile

    import httpx

    from tools.arm_runtime import (
        CLIENTS,
        COMPOSE,
        RUNTIME,
        Replica,
        command,
        isolated_environment,
        runtime_checks,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "work/arm-report.json")
    args = parser.parse_args()
    output = args.report.resolve()
    assert output.is_relative_to(ROOT / "work")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"schema_version": 1, "passed": False, "status": "running"}), encoding="utf-8"
    )
    started = datetime.now(UTC)
    fingerprint = source_fingerprint()
    image = json.loads(
        command(["docker", "image", "inspect", PROFILE.text("mockazure:arm")], "image")
    )[0]
    assert image["Config"]["Labels"]["org.mockazure.source-fingerprint"] == fingerprint, (
        "Rebuild M5 image for the current source fingerprint"
    )
    client_config = json.loads(command([*CLIENTS, "config", "--format", "json"], "client-config"))
    client_images = {
        name: json.loads(
            command(
                ["docker", "image", "inspect", client_config["services"][name]["image"]],
                name + "-image",
            )
        )[0]["Id"]
        for name in ("python", "powershell")
    }
    for service in ("api", "api2", "worker", "worker2"):
        container = command([*COMPOSE, "ps", "-q", service], service)
        assert (
            json.loads(command(["docker", "inspect", container], service + "-image"))[0]["Image"]
            == image["Id"]
        )
    network = json.loads(
        command(
            ["docker", "network", "inspect", PROFILE.text("mockazure-m5-clients-isolated")],
            "network",
        )
    )[0]
    assert network["Internal"]
    command(
        [
            sys.executable,
            "tools/prepare_clients.py",
            "--output",
            "work/vendor",
            "--manifest",
            "integrations/manifest.json",
            "--verify",
        ],
        "artifact-hashes",
    )
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv.exe")
    command([uv, "build", "--out-dir", "dist"], "wheel-build", timeout=180)
    wheels = list((ROOT / "dist").glob("mockazure-*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]
    sources = {
        p.relative_to(ROOT / "src/mockazure").as_posix(): sha(p)
        for p in (ROOT / "src/mockazure").rglob("*")
        if p.is_file() and p.suffix in {".py", ".json"}
    }
    with zipfile.ZipFile(wheel) as archive:
        for relative, digest in sources.items():
            import hashlib

            assert hashlib.sha256(archive.read("mockazure/" + relative)).hexdigest() == digest
    runtime_bytes = json.loads(
        command(
            [
                *COMPOSE,
                "exec",
                "-T",
                "api",
                "python",
                "-c",
                "import pathlib,json,hashlib,mockazure; "
                "root=pathlib.Path(mockazure.__file__).parent; "
                "print(json.dumps({p.relative_to(root).as_posix():"
                "hashlib.sha256(p.read_bytes()).hexdigest() "
                "for p in root.rglob('*') if p.is_file() and p.suffix in {'.py','.json'}}))",
            ],
            "runtime-bytes",
        )
    )
    assert runtime_bytes == sources
    directory = ROOT / "work" / ("arm-wheel-" + str(time.time_ns()))
    command([uv, "venv", "--python", sys.executable, directory], "wheel-venv")
    python = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    offline = {
        **os.environ,
        "UV_OFFLINE": "1",
        "UV_CACHE_DIR": str(ROOT.parents[1] / ".runtime/uv-cache"),
    }
    command([uv, "pip", "install", "--python", python, wheel], "wheel-install", env=offline)
    command(
        [python, "-c", "import mockazure; assert 'site-packages' in mockazure.__file__"],
        "wheel-origin",
        env=offline,
    )
    state = RUNTIME / "clients"
    config = json.loads((state / "native-state/client.json").read_text(encoding="utf-8"))
    checks = {"clean_wheel_install": True, "runtime_package_bytes": True}
    clients = {}
    replicas = [Replica(python, port) for port in (PROFILE.port(36444), PROFILE.port(36445))]
    for replica in replicas:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", replica.port))
    with httpx.Client(
        verify=ssl.create_default_context(cafile=str(RUNTIME / "instance/ca.pem")),
        trust_env=False,
        timeout=60,
        follow_redirects=False,
    ) as client:
        try:
            for replica in replicas:
                replica.start(client)
            checks.update(runtime_checks(client, config, *replicas))
            command(
                [
                    python,
                    "-c",
                    "import socket\n"
                    "for target in ('example.com','1.1.1.1','2606:4700:4700::1111'):\n"
                    " try:\n  socket.create_connection((target,443),timeout=1)\n"
                    " except PermissionError:\n  pass\n"
                    " else:\n  raise AssertionError('Network guard missing')\n",
                ],
                "native-network",
                env=isolated_environment(),
            )
            checks["native_network_blocked"] = True
            native_config = dict(config)
            native_config["endpoints"] = {name: replicas[0].base for name in config["endpoints"]}
            native_config_path = state / "native-state/qualified-client.json"
            native_config_path.write_text(json.dumps(native_config), encoding="utf-8")
            before = time.time()
            command(
                [
                    sys.executable,
                    "integrations/python/arm.py",
                    "--config",
                    native_config_path,
                ],
                "native-sdk",
                env=isolated_environment(),
                timeout=600,
            )
            path = state / "native-state/arm.json"
            assert path.stat().st_mtime >= before
            clients["native_python"] = json.loads(path.read_text(encoding="utf-8"))
        finally:
            for replica in replicas:
                replica.stop()
    for language, script in (
        ("python", "network_boundary.py"),
        ("powershell", "Test-NetworkBoundary.ps1"),
        ("python", "arm.py"),
        ("powershell", "Test-ArmManagement.ps1"),
    ):
        if script == "Test-ArmManagement.ps1":
            for label in ("application", "delegated", "denied_application", "denied_delegated"):
                name = label + "-token.json"
                shutil.copyfile(state / "python-state" / name, state / "powershell-state" / name)
        before = time.time()
        command(
            [*CLIENTS, "run", "--rm", language, f"/project/integrations/{language}/{script}"],
            script,
            timeout=600,
        )
        network_probe = script in ("network_boundary.py", "Test-NetworkBoundary.ps1")
        path = state / f"{language}-state" / ("network.json" if network_probe else "arm.json")
        assert path.stat().st_mtime >= before
        result = json.loads(path.read_text(encoding="utf-8-sig"))
        if network_probe:
            assert all(result[key] for key in ("dns_blocked", "ipv4_blocked", "ipv6_blocked"))
            checks[f"container_{language}_network_blocked"] = True
        else:
            clients[f"container_{language}"] = result
    for name in ("python", "powershell"):
        current = json.loads(
            command(
                ["docker", "image", "inspect", client_config["services"][name]["image"]],
                name + "-image-final",
            )
        )[0]["Id"]
        assert current == client_images[name]
    from tools.arm_runtime import capture_native_activity
    from tools.qualify_arm_collectors import qualify_collectors

    capture_native_activity(config)
    collectors = qualify_collectors()
    assert source_fingerprint() == fingerprint, "Source changed during qualification"
    report = {
        "schema_version": 1,
        "qualified_at": datetime.now(UTC).isoformat(),
        "started_at": started.isoformat(),
        "source_fingerprint": fingerprint,
        "source_commit": command(["git", "rev-parse", "HEAD"], "commit"),
        "manifest_sha256": sha(ROOT / "integrations/manifest.json"),
        "arm_references_sha256": sha(ROOT / "src/mockazure/providers/arm/data/references.json"),
        "lock_sha256": sha(ROOT / "uv.lock"),
        "wheel_sha256": sha(wheel),
        "runtime_image_id": image["Id"],
        "client_image_ids": client_images,
        "checks": checks,
        "clients": clients,
        "collector": collectors,
        "passed": collectors["passed"] is True,
        "total_seconds": (datetime.now(UTC) - started).total_seconds(),
        "limitations": [
            "PowerShell uses locally issued tokens; Python uses original MSAL authentication.",
            "Supported ARM versions and four immutable built-in roles are explicit.",
            "M4's four original collector output failures remain combined-release blockers.",
        ],
    }
    try:
        validate_report(report)
    except (AssertionError, KeyError, ValueError) as error:
        report["passed"] = False
        report["validation_error"] = str(error) or type(error).__name__
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "checks": len(checks),
                "clients": len(clients),
                "collector_cases": len(collectors["cases"]),
                "seconds": report["total_seconds"],
            },
            indent=2,
        ),
        flush=True,
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        import json
        import sys

        destination = ROOT / "work/arm-report.json"
        if "--report" in sys.argv:
            destination = Path(sys.argv[sys.argv.index("--report") + 1]).resolve()
        if destination.is_relative_to(ROOT / "work"):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "passed": False,
                        "error_type": type(error).__name__,
                        "qualified_at": datetime.now(UTC).isoformat(),
                        "source_fingerprint": source_fingerprint(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        raise

"""Validate evidence for real-client M4 runtime qualification."""

import hashlib
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.qualification_profile import profile_for  # noqa: E402

PROFILE = profile_for("m4")
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
RUNTIME_CHECKS = frozenset(
    {
        "clean_wheel_install",
        "runtime_package_bytes",
        "two_api_replicas",
        "two_workers",
        "api_restart",
        "postgres_restart",
        "worker_restart",
        "audit_pending_restart",
        "audit_snapshot_resume",
        "rule_snapshot_resume",
        "tenant_isolation",
        "role_denied",
        "licence_denied",
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
        "rule_crud",
        "rule_actions",
        "rule_paging",
        "audit_query",
        "audit_paging",
        "audit_filters",
        "native_evidence",
        "permission_denied",
    }
)
EXCHANGE_CHECKS = frozenset(
    {
        "Get-EXOMailbox",
        "Get-EXOMailboxStatistics",
        "Get-EXOMailboxFolderStatistics",
        "Get-EXOCasMailbox",
        "ArchiveStatistics",
        "CommandLifecycle",
        "TraceAndDetail",
    }
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_fingerprint(root=ROOT):
    files = {
        root / name
        for name in (
            "pyproject.toml",
            "uv.lock",
            "build-requirements.txt",
            "Dockerfile",
            "integrations/Dockerfile.python",
            PROFILE.text("deploy/compose/milestones/compose.m4.yaml"),
            PROFILE.text("deploy/compose/milestones/compose.clients.m4.yaml"),
            PROFILE.text("deploy/haproxy-m4.cfg"),
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
    assert report["schema_version"] == 1 and report["passed"] is True
    age = datetime.now(UTC) - datetime.fromisoformat(report["qualified_at"])
    assert timedelta(0) <= age < timedelta(days=1)
    from tools.collector_profiles import PROFILES
    from tools.qualify_mail_collectors import FUNCTIONS, validate_cases

    profile = report.get("collector_profile", "original")
    assert profile in PROFILES
    assert all(item.get("profile", "original") == profile for item in report["collectors"].values())
    if profile != "original":
        baseline = report["upstream_baseline"]
        assert set(baseline) == {"hawk_rules", "mes_trace"}
        for label, item in baseline.items():
            assert item.get("run_hashes"), "Missing original run evidence"
            assert item["profile"] == "original" and item["passed"] is False
            assert len(item["cases"]) == 4
            assert {(c["mode"], c["denied"]) for c in item["cases"]} == {
                (mode, denied) for mode in ("application", "delegated") for denied in (False, True)
            }
            for case in item["cases"]:
                assert type(case["denied"]) is bool
                assert case["passed"] is case["denied"]
                if not case["denied"]:
                    assert isinstance(case.get("failure"), str) and case["failure"]
                    assert (
                        case.get("failure_code")
                        == {
                            "hawk_rules": "hawk_json_append",
                            "mes_trace": "mes_csv_timestamp_precision",
                        }[label]
                    )
        assert all(item.get("run_hashes") for item in report["collectors"].values())
    assert set(report["collectors"]) == set(FUNCTIONS)
    for result in report["collectors"].values():
        assert result["passed"] is True
        assert type(result["records_verified"]) is int and result["records_verified"] > 0
        assert result["source_hashes"] and result["output_hashes"]
        validate_cases(result["cases"])
    assert set(report["checks"]) == RUNTIME_CHECKS
    assert all(value is True for value in report["checks"].values())
    assert set(report["clients"]) == {"native_python", "container_python", "container_powershell"}
    for result in report["clients"].values():
        assert result["passed"] is True and set(result["modes"]) == {"application", "delegated"}
        for checks in result["modes"].values():
            assert set(checks) == CLIENT_CHECKS and all(value is True for value in checks.values())
    exchange = report["exchange"]
    assert exchange["passed"] is True and exchange["version"] == "3.11.0"
    assert set(exchange["modes"]) == {"application", "delegated"}
    for checks in exchange["modes"].values():
        assert set(checks) == EXCHANGE_CHECKS and all(value is True for value in checks.values())


def validate_report(report, *, root=ROOT):
    validate_results(report)
    from tools.qualify_mail_collectors import validate_collector_hashes

    validate_collector_hashes(report["collectors"], root)
    if report.get("collector_profile", "original") != "original":
        validate_collector_hashes(report["upstream_baseline"], root)
    assert report["source_fingerprint"] == source_fingerprint(root)
    assert re.fullmatch("[0-9a-f]{40}", report["source_commit"])
    assert report["manifest_sha256"] == sha(root / "integrations/manifest.json")
    assert report["lock_sha256"] == sha(root / "uv.lock")
    wheels = list((root / "dist").glob("mockazure-*.whl"))
    assert len(wheels) == 1 and report["wheel_sha256"] == sha(wheels[0])
    assert re.fullmatch("sha256:[0-9a-f]{64}", report["runtime_image_id"])
    assert set(report["client_image_ids"]) == {"python", "powershell"}
    assert all(
        re.fullmatch("sha256:[0-9a-f]{64}", value) for value in report["client_image_ids"].values()
    )
    for name in ("native_python", "container_python"):
        assert report["clients"][name]["versions"] == {
            "msal": "1.39.0",
            "msgraph-sdk": "1.64.0",
            "azure-identity": "1.26.0",
        }


def main():
    import argparse
    import os
    import shutil
    import socket
    import ssl
    import sys
    import time
    import zipfile

    import httpx
    from mail_runtime import (
        CLIENTS,
        COMPOSE,
        RUNTIME,
        Replica,
        command,
        isolated_environment,
        runtime_checks,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "work/mail-report.json")
    parser.add_argument(
        "--collector-profile", choices=("original", "serialization-v1"), default="original"
    )
    args = parser.parse_args()
    output = args.report.resolve()
    assert output.is_relative_to(ROOT / "work")
    started = datetime.now(UTC)
    fingerprint = source_fingerprint()
    image = json.loads(
        command(["docker", "image", "inspect", PROFILE.text("mockazure:mail")], "image")
    )[0]
    assert image["Config"]["Labels"]["org.mockazure.source-fingerprint"] == fingerprint, (
        "Rebuild M4 image for the current source fingerprint"
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
            ["docker", "network", "inspect", PROFILE.text("mockazure-m4-clients-isolated")],
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
    directory = ROOT / "work" / ("mail-wheel-" + str(time.time_ns()))
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
    config = json.loads((state / "native-state/client.json").read_text())
    checks = {"clean_wheel_install": True, "runtime_package_bytes": True}
    clients = {}
    replicas = [Replica(python, port) for port in (PROFILE.port(34444), PROFILE.port(34445))]
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
            native_config_path.write_text(json.dumps(native_config))
            before = time.time()
            command(
                [
                    sys.executable,
                    "integrations/python/graph_mail.py",
                    "--config",
                    native_config_path,
                ],
                "native-sdk",
                env=isolated_environment(),
                timeout=600,
            )
            path = state / "native-state/graph-mail.json"
            assert path.stat().st_mtime >= before
            clients["native_python"] = json.loads(path.read_text())
        finally:
            for replica in replicas:
                replica.stop()
    for language, script in (
        ("python", "network_boundary.py"),
        ("powershell", "Test-NetworkBoundary.ps1"),
        ("python", "graph_mail.py"),
        ("powershell", "Test-GraphMail.ps1"),
    ):
        if script == "Test-GraphMail.ps1":
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
        path = (
            state / f"{language}-state" / ("network.json" if network_probe else "graph-mail.json")
        )
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
    exchange_config = dict(config)
    exchange_config["endpoints"] = {
        name: PROFILE.text("https://localhost:34443") for name in config["endpoints"]
    }
    exchange_config_path = state / "native-state/exchange-client.json"
    exchange_config_path.write_text(json.dumps(exchange_config))
    command(
        [
            sys.executable,
            "integrations/python/exchange_mail_tokens.py",
            "--config",
            exchange_config_path,
        ],
        "exchange-tokens",
        env=isolated_environment(),
    )
    shutil.copyfile(
        state / "native-state/exchange-delegated-token.json",
        state / "powershell-state/exchange-delegated-token.json",
    )
    command(
        [
            *CLIENTS,
            "run",
            "--rm",
            "powershell",
            "/project/integrations/powershell/Test-ExchangeMail.ps1",
        ],
        "exchange-module",
        timeout=600,
    )
    exchange = json.loads(
        (state / "powershell-state/exchange-mail.json").read_text(encoding="utf-8-sig")
    )
    from tools.qualify_mail_collectors import qualify_collectors

    baseline = {}
    if args.collector_profile != "original":
        baseline = qualify_collectors(labels={"hawk_rules", "mes_trace"})
        # Preserve the baseline even if the subsequent patched process aborts.
        baseline_path = output.parent / f"m4-upstream-baseline-{time.time_ns()}.json"
        with baseline_path.open("x", encoding="utf-8") as stream:
            json.dump({"source_fingerprint": fingerprint, "collectors": baseline}, stream, indent=2)
    collectors = qualify_collectors(profile=args.collector_profile)
    assert source_fingerprint() == fingerprint, "Source changed during qualification"
    report = {
        "schema_version": 1,
        "qualified_at": datetime.now(UTC).isoformat(),
        "started_at": started.isoformat(),
        "source_fingerprint": fingerprint,
        "source_commit": command(["git", "rev-parse", "HEAD"], "commit"),
        "manifest_sha256": sha(ROOT / "integrations/manifest.json"),
        "lock_sha256": sha(ROOT / "uv.lock"),
        "wheel_sha256": sha(wheel),
        "runtime_image_id": image["Id"],
        "client_image_ids": client_images,
        "checks": checks,
        "clients": clients,
        "exchange": exchange,
        "collector_profile": args.collector_profile,
        "upstream_baseline": baseline,
        "collectors": collectors,
        "passed": all(item["passed"] is True for item in collectors.values()),
        "total_seconds": (datetime.now(UTC) - started).total_seconds(),
        "limitations": [
            "PowerShell uses local tokens; Python uses real MSAL authentication flows.",
            "Mailbox configuration and audit evidence do not simulate mail delivery.",
            "5,000-user qualification is not a throughput SLA.",
        ],
    }
    # Preserve failed evidence as well as successes. Validation remains strict:
    # an unresolved original collector artifact error exits nonzero.
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    validate_report(report)
    print(json.dumps({"passed": True, "checks": len(checks), "clients": len(clients)}))


if __name__ == "__main__":
    main()

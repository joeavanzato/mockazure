"""Qualify this checkout's local Compose instance; never target arbitrary databases."""

import argparse
import hashlib
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes

from mockazure.config import load_settings

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "--env-file", str(ROOT / ".runtime/compose.env")]
GUARD = ROOT / "tests/support/network_guard"


def command(args, *, env=None, cwd=ROOT, timeout=180):
    result = subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode:
        # All logs remain local; no DSNs or tokens are arguments.
        raise RuntimeError(f"Qualification command failed: {args[0]} (exit {result.returncode})")
    return result.stdout.strip()


def wait_ready(client, base, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.get(base + "/_mock/ready").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError("HTTPS readiness timeout")


def get_json(client, method, url, status=200, **kwargs):
    response = client.request(method, url, **kwargs)
    if response.status_code != status:
        raise RuntimeError(
            f"Unexpected management status {response.status_code}, expected {status}"
        )
    return response.json()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fingerprint(path):
    return x509.load_pem_x509_certificate(path.read_bytes()).fingerprint(hashes.SHA256()).hex()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.config.resolve() != ROOT / ".runtime/instance/native.json":
        parser.error("Verifier only recreates this checkout's dedicated Compose instance")
    report_path = args.report.resolve()
    if not report_path.is_relative_to(ROOT / "work"):
        parser.error("Report must stay under this checkout's work directory")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # A failed run must never leave a stale success report.
    if report_path.exists():
        report_path.unlink()
    settings = load_settings(args.config, {})
    base = str(settings.admin_base_url).rstrip("/")
    report = {"schema_version": 1, "external_service_requests": 0}
    report["ca_fingerprint_before"] = fingerprint(settings.ca_file)
    token_before = settings.token_file.read_bytes()
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("MOCKAZURE_")
    }
    environment["PYTHONPATH"] = str(GUARD)
    guard_log = ROOT / "work/network-guard.log"
    environment["MOCKAZURE_TEST_NETWORK_LOG"] = str(guard_log)
    probe = (
        "import socket\ntry: socket.create_connection(('example.invalid',443),timeout=1)\n"
        "except PermissionError: print('blocked-by-guard')\nelse: raise SystemExit(1)"
    )
    assert command([sys.executable, "-c", probe], env=environment) == "blocked-by-guard"
    report["native_external_blocked"] = True

    # Install the guard in the verifier itself after dependency acquisition.
    sys.path.insert(0, str(GUARD))
    import sitecustomize  # noqa: F401

    # Verify isolated network membership, plus an attempted external TCP connection.
    probe_container = (
        "import socket\ntry: s=socket.create_connection(('1.1.1.1',443),timeout=2)\n"
        "except OSError: print('blocked')\nelse: s.close(); raise SystemExit(1)"
    )
    for service in ("api", "worker", "db"):
        ids = command([*COMPOSE, "ps", "-q", service]).splitlines()
        assert ids
        for container_id in ids:
            detail = json.loads(command(["docker", "inspect", container_id]))[0]
            for network in detail["NetworkSettings"]["Networks"]:
                assert json.loads(command(["docker", "network", "inspect", network]))[0]["Internal"]
        if service != "db":
            assert (
                command([*COMPOSE, "exec", "-T", service, "python", "-c", probe_container])
                == "blocked"
            )
    report["container_external_blocked"] = True

    # Cold initialization uses only the already-built image with networking disabled.
    cold = ROOT / "work" / ("docker-bootstrap-" + uuid4().hex)
    cold.mkdir()
    command(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--mount",
            f"type=bind,source={cold},target=/project",
            "mockazure:local",
            "init",
            "--compose",
            "--project-dir",
            "/project",
            "--port",
            "18443",
            "--db-port",
            "15432",
        ]
    )
    cold_settings = load_settings(cold / ".runtime/instance/native.json", {})
    assert cold_settings.data_dir == cold / ".runtime/instance"
    report["docker_only_bootstrap"] = True

    processes, streams = [], []
    context = ssl.create_default_context(cafile=str(settings.ca_file))
    with httpx.Client(
        verify=context,
        trust_env=False,
        timeout=10,
        headers={"Authorization": "Bearer " + token_before.decode().strip()},
    ) as client:
        wait_ready(client, base)
        assert get_json(client, "GET", base + "/_mock/capabilities")["provider_operations"] == []
        get_json(client, "GET", base + "/v1.0/users", status=501)
        report["tls_verified"] = True
        suffix = uuid4().hex
        tenant = get_json(
            client,
            "POST",
            base + "/_mock/tenants",
            status=201,
            json={
                "slug": "verify-" + suffix,
                "display_name": "Qualification",
                "domain": suffix + ".test",
            },
        )
        try:
            second_port = free_port()
            env = {
                **environment,
                "MOCKAZURE_LISTEN_PORT": str(second_port),
                "MOCKAZURE_ADMIN_BASE_URL": f"https://localhost:{second_port}",
            }
            stream = (ROOT / "work/native-api.log").open("w")
            streams.append(stream)
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "mockazure",
                        "serve",
                        "--config",
                        str(args.config.resolve()),
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=stream,
                    stderr=stream,
                )
            )
            second_base = f"https://localhost:{second_port}"
            wait_ready(client, second_base)
            assert get_json(client, "GET", second_base + "/_mock/tenants/" + tenant["id"]) == tenant
            report["two_api_processes"] = True
            command([*COMPOSE, "up", "-d", "--scale", "worker=2", "worker"])
            ids = command([*COMPOSE, "ps", "-q", "worker"]).splitlines()
            assert len(ids) == 2
            jobs = [
                get_json(
                    client, "POST", base + f"/_mock/tenants/{tenant['id']}/exports", status=202
                )
                for _ in range(12)
            ]
            for job in jobs:
                url = base + f"/_mock/tenants/{tenant['id']}/jobs/{job['id']}"
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    state = get_json(client, "GET", url)
                    if state["state"] == "succeeded":
                        break
                    if state["state"] == "failed":
                        raise RuntimeError("Export worker failed")
                    time.sleep(0.1)
                assert state["state"] == "succeeded" and state["attempts"] == 1
                result = get_json(client, "GET", url + "/result")
                assert result["source_tenant_id"] == tenant["id"]
            report["two_workers_no_duplicate_exports"] = True
            # Stop native process too: no live pooled connections during database recreation.
            processes[0].terminate()
            processes[0].wait(timeout=15)
            db_id = command([*COMPOSE, "ps", "-q", "db"])
            details = json.loads(command(["docker", "inspect", db_id]))[0]
            mount = next(
                item for item in details["Mounts"] if item["Destination"] == "/var/lib/postgresql"
            )
            assert mount["Type"] == "bind"
            report["postgres_mount"] = "/var/lib/postgresql"
            command([*COMPOSE, "down"])
            command([*COMPOSE, "up", "-d", "--scale", "worker=2"])
            assert command([*COMPOSE, "ps", "-q", "db"]) != db_id
            wait_ready(client, base)
            assert get_json(client, "GET", base + "/_mock/tenants/" + tenant["id"]) == tenant
            for job in jobs:
                result = get_json(
                    client, "GET", base + f"/_mock/tenants/{tenant['id']}/jobs/{job['id']}/result"
                )
                assert result["source_tenant_id"] == tenant["id"]
            report["tenant_survived_recreate"] = True
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=15)
            for stream in streams:
                stream.close()
    report["ca_fingerprint_after"] = fingerprint(settings.ca_file)
    report["admin_token_unchanged"] = settings.token_file.read_bytes() == token_before

    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv must be available for offline wheel qualification")
    wheel = next((ROOT / "dist").glob("mockazure-*.whl"))
    wheel_dir = ROOT / "work" / ("wheel-" + uuid4().hex)
    command([uv, "venv", "--python", sys.executable, wheel_dir], env=environment)
    interpreter = wheel_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    offline = {**environment, "UV_OFFLINE": "1"}
    command([uv, "pip", "install", "--python", interpreter, wheel], env=offline)
    assert (
        command([interpreter, "-m", "mockazure", "--version"], env=offline, cwd=wheel_dir)
        == "0.1.0.dev0"
    )
    check = (
        "import mockazure; from mockazure.db.migrations import configuration;"
        "from alembic.script import ScriptDirectory;"
        "assert 'site-packages' in mockazure.__file__;"
        "assert ScriptDirectory.from_config(configuration()).get_current_head()=='002'"
    )
    command([interpreter, "-c", check], env=offline, cwd=wheel_dir)
    report["wheel_install_passed"] = True
    report["wheel_sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    report["python_version"] = sys.version.split()[0]
    report["qualified_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    assert report["ca_fingerprint_before"] == report["ca_fingerprint_after"]
    assert report["admin_token_unchanged"]
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print("Foundation qualification passed; report: " + str(report_path))


if __name__ == "__main__":
    main()

"""Smoke-test a fresh ordinary Compose lab; preserve data and never touch another project."""

import hashlib
import json
import os
import shutil
import socket
import ssl
import subprocess
import time
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def default_ports():
    # Docker publishes seven adjacent service ports. A free base port does not
    # imply its neighbours are bindable (notably Windows excluded ranges).
    for _ in range(100):
        api_port = free_port()
        if api_port > 65529:
            continue
        try:
            with ExitStack() as sockets:
                for port in range(api_port, api_port + 7):
                    sock = sockets.enter_context(socket.socket())
                    sock.bind(("127.0.0.1", port))
                db_port = free_port()
                if db_port in range(api_port, api_port + 7):
                    continue
                sock = sockets.enter_context(socket.socket())
                sock.bind(("127.0.0.1", db_port))
                return api_port, db_port
        except OSError:
            continue
    raise RuntimeError("No available local service port range was found")


def wait_for_service_origins(client, origins, *, attempts=120):
    """Wait for each TLS proxy listener, after the core database health check.

    Data listeners own their routes and return a service-shaped 4xx or explicit
    MockAzureOperationNotImplemented for this unauthenticated probe. A completed
    TLS/HTTP response establishes transport readiness only. Original SDK
    workflows must still prove every operation.
    """
    pending = {origin.rstrip("/") for origin in origins}
    for _ in range(attempts):
        for origin in tuple(pending):
            try:
                response = client.get(origin + "/_mock/ready", timeout=2)
                unsupported_probe = (
                    response.status_code == 501
                    and response.headers.get("x-ms-error-code")
                    == "MockAzureOperationNotImplemented"
                )
                if response.status_code in {200, 400, 401, 403, 404} or unsupported_probe:
                    pending.remove(origin)
            except httpx.HTTPError:
                pass
        if not pending:
            return
        time.sleep(0.5)
    raise TimeoutError("Local service origins did not recover: " + ", ".join(sorted(pending)))


def main(*, qualify=None):
    run_id = uuid4().hex[:12]
    directory = ROOT / "work" / ("default-smoke-" + run_id)
    directory.mkdir(parents=True)
    project = "mockazure-default-" + run_id
    image = "mockazure:default-" + run_id
    api_port, db_port = default_ports()
    shutil.copyfile(ROOT / "compose.yaml", directory / "compose.yaml")
    (directory / "deploy").mkdir()
    for name in ("haproxy.cfg", "postgres-guard.sh"):
        shutil.copyfile(ROOT / "deploy" / name, directory / "deploy" / name)
    (directory / "image.yaml").write_text(
        "services:\n"
        + "".join(
            f"  {service}:\n    image: {image}\n"
            for service in ("api", "worker", "migrate", "identity-init")
        ),
        encoding="utf-8",
    )
    compose = [
        "docker",
        "compose",
        "-p",
        project,
        "--env-file",
        ".runtime/compose.env",
        "-f",
        "compose.yaml",
        "-f",
        "image.yaml",
    ]
    report = {
        "project": project,
        "started_at": datetime.now(UTC).isoformat(),
        "passed": False,
        "checks": [],
        "source_files": {
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
            for p in [
                "compose.yaml",
                "deploy/postgres-guard.sh",
                "deploy/haproxy.cfg",
                "Dockerfile",
                "pyproject.toml",
                "uv.lock",
                "README.md",
                "LICENSE",
                "NOTICE",
                "build-requirements.txt",
            ]
            + sorted(
                path.relative_to(ROOT).as_posix()
                for path in (ROOT / "src").rglob("*")
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
            )
        },
    }
    report_path = directory / "report.json"

    def save():
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def command(args, *, cwd=directory, capture=False, timeout=600):
        with (directory / "process.log").open("a", encoding="utf-8") as log:
            result = subprocess.run(
                args,
                cwd=cwd,
                text=True,
                timeout=timeout,
                stdout=subprocess.PIPE if capture else log,
                stderr=log,
            )
        if result.returncode:
            raise RuntimeError(f"Local command failed ({result.returncode}); see process.log")
        return result.stdout.strip() if capture else None

    def check(name):
        report["checks"].append(name)
        save()
        print(name, flush=True)

    def ready(client):
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            try:
                if client.get("/_mock/ready").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        raise TimeoutError("Default runtime readiness failed")

    save()
    started = False
    try:
        command(["docker", "build", "-t", image, "."], cwd=ROOT)
        command(
            [
                "docker",
                "run",
                "--rm",
                # Linux bind mounts retain the creating container UID. Bootstrap
                # as the caller so private 0600 files stay readable by the harness.
                *(["--user", f"{os.getuid()}:{os.getgid()}"] if os.name == "posix" else []),
                "--network",
                "none",
                "--mount",
                f"type=bind,source={directory},target=/project",
                image,
                "init",
                "--compose",
                "--project-dir",
                "/project",
                "--port",
                str(api_port),
                "--db-port",
                str(db_port),
            ]
        )
        check("docker_only_initialization")
        instance = directory / ".runtime/instance"
        identity = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in instance.iterdir()
            if p.is_file()
        }
        started = True
        command(compose + ["up", "-d", "--no-build"])
        context = ssl.create_default_context(cafile=str(instance / "ca.pem"))
        with httpx.Client(
            base_url=f"https://localhost:{api_port}", verify=context, trust_env=False, timeout=30
        ) as client:
            ready(client)
            check("https_schema_identity_ready")
            token = (instance / "admin.token").read_text().strip()
            headers = {"Authorization": "Bearer " + token}
            response = client.post(
                "/_mock/tenants",
                headers=headers,
                json={
                    "slug": "default-" + run_id,
                    "display_name": "Default runtime smoke",
                    "domain": "default-" + run_id + ".test",
                    "licences": ["E5"],
                },
            )
            assert response.status_code == 201
            tenant = response.json()
            check("tenant_create")
            db = command(compose + ["ps", "-q", "db"], capture=True)
            inspected = json.loads(command(["docker", "inspect", db], capture=True))[0]
            mount = next(
                m for m in inspected["Mounts"] if m["Destination"] == "/var/lib/postgresql"
            )
            assert mount["Type"] == "volume" and mount["Name"] == project + "_postgres-data"
            report["storage"] = {"type": mount["Type"], "volume": mount["Name"]}
            check("linux_volume")
            command(compose + ["stop", "--timeout", "120"])
            command(compose + ["up", "-d", "--no-build"])
            ready(client)
            response = client.get("/_mock/tenants/" + tenant["id"], headers=headers)
            assert response.status_code == 200 and response.json() == tenant
            assert all(
                hashlib.sha256((instance / name).read_bytes()).hexdigest() == digest
                for name, digest in identity.items()
            )
            check("restart_tenant_and_identity_preserved")

            # Substitute empty ephemeral storage, never the owned database volume.
            guard = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--tmpfs",
                    "/var/lib/postgresql",
                    "--mount",
                    f"type=bind,source={instance},target=/instance,readonly",
                    "--mount",
                    f"type=bind,source={directory / 'deploy/postgres-guard.sh'},"
                    "target=/guard.sh,readonly",
                    "--entrypoint",
                    "/bin/sh",
                    inspected["Config"]["Image"],
                    "/guard.sh",
                    "postgres",
                ],
                cwd=directory,
                text=True,
                capture_output=True,
                timeout=60,
            )
            (directory / "missing-storage.log").write_text(
                guard.stdout + guard.stderr, encoding="utf-8"
            )
            assert guard.returncode == 1, "Missing storage was not refused"
            assert "previously initialized database storage is missing" in guard.stderr
            assert client.get("/_mock/tenants/" + tenant["id"], headers=headers).json() == tenant
            check("missing_storage_refused_original_tenant_preserved")
            if qualify:
                qualify(directory, command, compose, client, headers, tenant, check)
        report["passed"] = True
    except Exception as error:
        report["error_type"] = type(error).__name__
        raise
    finally:
        try:
            if started:
                command(compose + ["logs", "--no-color"])
                # This unique disposable qualification project owns these containers/networks.
                # Retain PostgreSQL volumes and instance files for inspection/recovery.
                command(compose + ["down", "--timeout", "120"])
                report["containers_stopped"] = True
                report["ephemeral_networks_removed"] = True
        except Exception as error:
            report["passed"] = False
            report["cleanup_error_type"] = type(error).__name__
            raise
        finally:
            report["completed_at"] = datetime.now(UTC).isoformat()
            save()
            print(json.dumps({"report": str(report_path), "passed": report["passed"]}), flush=True)


if __name__ == "__main__":
    main()

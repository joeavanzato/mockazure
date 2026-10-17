"""Smoke-test a fresh ordinary Compose lab; preserve data and never touch another project."""

import hashlib
import json
import shutil
import socket
import ssl
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    run_id = uuid4().hex[:12]
    directory = ROOT / "work" / ("default-smoke-" + run_id)
    directory.mkdir(parents=True)
    project = "mockazure-default-" + run_id
    image = "mockazure:default-" + run_id
    api_port, db_port = free_port(), free_port()
    while db_port == api_port:
        db_port = free_port()
    shutil.copyfile(ROOT / "compose.yaml", directory / "compose.yaml")
    (directory / "deploy").mkdir()
    shutil.copyfile(ROOT / "deploy/haproxy.cfg", directory / "deploy/haproxy.cfg")
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
            for p in ("compose.yaml", "src/mockazure/bootstrap.py")
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
        report["passed"] = True
    except Exception as error:
        report["error_type"] = type(error).__name__
        raise
    finally:
        if started:
            command(compose + ["stop", "--timeout", "120"])
            report["containers_stopped"] = True
        report["completed_at"] = datetime.now(UTC).isoformat()
        save()
        print(json.dumps({"report": str(report_path), "passed": report["passed"]}), flush=True)


if __name__ == "__main__":
    main()

"""Reproduce isolated M2 qualification against this checkout's dedicated lab only."""

import argparse
import base64
import hashlib
import html
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from mockazure.config import load_settings

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CLIENT_COMPOSE = [
    "docker",
    "compose",
    "-f",
    "compose.clients.yaml",
    "-f",
    "compose.clients.m2.yaml",
]
M2_COMPOSE = ["docker", "compose", "-f", "compose.m2.yaml"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_fingerprint():
    files = [
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
        ROOT / "build-requirements.txt",
        ROOT / "Dockerfile",
        ROOT / "integrations/Dockerfile.python",
        ROOT / "deploy/haproxy-m2.cfg",
        ROOT / "compose.m2.yaml",
        ROOT / "compose.clients.yaml",
        ROOT / "compose.clients.m2.yaml",
    ]
    from tools.source_inputs import qualification_files

    files.extend(qualification_files(ROOT))
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def command(args, label, *, env=None, timeout=180):
    result = subprocess.run(
        [str(a) for a in args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout
    )
    (ROOT / "work" / f"identity-{label}.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"Qualification failed: {label}; inspect its project-local log")
    return result.stdout.strip()


def wait_ready(client, base):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if client.get(base + "/_mock/ready").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError("Local identity readiness timed out")


def checked(response, status=200):
    if response.status_code != status:
        raise RuntimeError(f"Local identity returned {response.status_code}, expected {status}")
    return response.json()


def browser_code(client, base, config, user):
    verifier = "v" * 64
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    response = client.get(
        base + f"/{config['tenant_id']}/oauth2/v2.0/authorize",
        params={
            "client_id": config["public_client_id"],
            "response_type": "code",
            "redirect_uri": "http://localhost:8123/callback",
            "scope": "openid User.Read offline_access",
            "state": "replica-verification",
            "nonce": "replica-nonce",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    if response.status_code != 200:
        raise RuntimeError("Browser authorization failed")
    fields = {
        name: html.unescape(re.search(r'name="' + name + r'" value="([^"]*)"', response.text)[1])
        for name in ("transaction", "csrf")
    }
    for path, data in (
        ("/login", user),
        ("/consent", {"decision": "approve"}),
        ("/mfa", {"decision": "complete"}),
    ):
        response = client.post(base + path, data={**fields, **data})
        if response.status_code not in (200, 303):
            raise RuntimeError("Local browser approval failed")
    values = parse_qs(urlsplit(response.headers["location"]).query)
    assert values["state"] == ["replica-verification"]
    return {
        "grant_type": "authorization_code",
        "client_id": config["public_client_id"],
        "redirect_uri": "http://localhost:8123/callback",
        "code": values["code"][0],
        "code_verifier": verifier,
    }


def qualify_replicas(settings, python, config):
    base = str(settings.admin_base_url).rstrip("/")
    state = ROOT / ".runtime/clients/powershell-state"
    secret = json.loads((state / "secret.json").read_text())["secret"]
    user = json.loads((state / "user.json").read_text())
    admin = {"Authorization": "Bearer " + settings.token_file.read_text().strip()}
    before = {
        name: (settings.data_dir / name).read_bytes()
        for name in ("admin.token", "ca.pem", "identity.wrap")
    }
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    replica = f"https://localhost:{port}"
    values = settings.model_dump(mode="json")
    values.update(
        data_dir=str(settings.data_dir),
        listen_host="127.0.0.1",
        listen_port=port,
        database_url=settings.database_url.get_secret_value(),
    )
    path = ROOT / ".runtime/m2/replica.json"
    path.write_text(json.dumps(values))
    environment = {k: v for k, v in os.environ.items() if not k.startswith("MOCKAZURE_")}
    environment["PYTHONPATH"] = str(ROOT / "tests/support/network_guard")
    with (ROOT / "work/identity-replica.log").open("w") as log:
        process = subprocess.Popen(
            [str(python), "-m", "mockazure", "serve", "--config", str(path)],
            cwd=ROOT / "work",
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(
                verify=ssl.create_default_context(cafile=str(settings.ca_file)),
                trust_env=False,
                timeout=15,
            ) as client:
                wait_ready(client, replica)
                path_token = f"/{config['tenant_id']}/oauth2/v2.0/token"
                fields = {
                    "grant_type": "client_credentials",
                    "client_id": config["client_id"],
                    "client_secret": secret,
                    "scope": "https://graph.microsoft.com/.default",
                }
                tokens = [
                    checked(client.post(origin + path_token, data=fields))["access_token"]
                    for origin in (base, replica)
                ]
                for origin, raw in zip((replica, base), tokens, strict=True):
                    result = checked(
                        client.get(
                            origin + "/v1.0/organization",
                            headers={"Authorization": "Bearer " + raw},
                        )
                    )
                    assert result["value"][0]["id"] == config["tenant_id"]
                code = browser_code(client, base, config, user)
                with ThreadPoolExecutor(2) as executor:
                    responses = list(
                        executor.map(
                            lambda origin: client.post(origin + path_token, data=code),
                            (base, replica),
                        )
                    )
                assert sorted(r.status_code for r in responses) == [200, 400]
                assert (
                    next(r.json() for r in responses if r.status_code == 400)["error"]
                    == "invalid_grant"
                )
                delegated = next(r.json() for r in responses if r.status_code == 200)
                refresh = {
                    "grant_type": "refresh_token",
                    "client_id": config["public_client_id"],
                    "refresh_token": delegated["refresh_token"],
                }
                with ThreadPoolExecutor(2) as executor:
                    renewed = list(
                        executor.map(
                            lambda origin: checked(client.post(origin + path_token, data=refresh)),
                            (base, replica),
                        )
                    )
                sessions = checked(
                    client.get(
                        base + f"/_mock/tenants/{config['tenant_id']}/identity/sessions",
                        headers=admin,
                        params={"limit": 500},
                    )
                )["items"]
                # Revoke the session identified in the token, never unrelated sessions.
                import jwt

                session_id = jwt.decode(
                    delegated["access_token"], options={"verify_signature": False}
                )["sid"]
                assert any(row["id"] == session_id for row in sessions)
                checked(
                    client.delete(
                        base
                        + f"/_mock/tenants/{config['tenant_id']}/identity/sessions/{session_id}",
                        headers=admin,
                    )
                )
                for token in [delegated, *renewed]:
                    response = client.post(
                        replica + path_token,
                        data={**refresh, "refresh_token": token["refresh_token"]},
                    )
                    assert (
                        response.status_code == 400 and response.json()["error"] == "invalid_grant"
                    )
                kid = checked(client.post(base + "/_mock/identity/keys/rotate", headers=admin))[
                    "kid"
                ]
                keys = checked(client.get(replica + f"/{config['tenant_id']}/discovery/v2.0/keys"))
                assert kid in [key["kid"] for key in keys["keys"]]
                assert (
                    client.get(
                        replica + "/v1.0/organization",
                        headers={"Authorization": "Bearer " + tokens[0]},
                    ).status_code
                    == 200
                )
                # Restart M2 DB/API while the native replica keeps its connection pool.
                command([*M2_COMPOSE, "restart", "db", "api"], "restart", timeout=90)
                wait_ready(client, base)
                wait_ready(client, replica)
                assert (
                    checked(client.get(base + f"/{config['tenant_id']}/discovery/v2.0/keys"))
                    == keys
                )
                assert client.post(replica + path_token, data=refresh).status_code == 400
                assert client.post(base + path_token, data=fields).status_code == 200
                assert (
                    checked(
                        client.get(
                            base + "/v1.0/organization",
                            headers={"Authorization": "Bearer " + tokens[0]},
                        )
                    )["value"][0]["id"]
                    == config["tenant_id"]
                )
                assert all(
                    (settings.data_dir / name).read_bytes() == data for name, data in before.items()
                )
        finally:
            process.terminate()
            process.wait(timeout=20)
    return {
        name: True
        for name in (
            "native_wheel_https",
            "cross_replica_tokens",
            "single_code_redemption",
            "concurrent_refresh",
            "revocation_all_handles",
            "rotation_old_tokens",
            "database_api_restart",
            "identity_files_preserved",
        )
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--client-manifest", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.config.resolve() != ROOT / ".runtime/m2/instance/native.json":
        # Accept the plan's conventional alias if explicitly supplied.
        if args.config.resolve() != ROOT / ".runtime/m2/native.json":
            parser.error("Only this checkout's dedicated M2 instance may be qualified")
    config_path = ROOT / ".runtime/m2/instance/native.json"
    settings = load_settings(config_path)
    report_path = args.report.resolve()
    if not report_path.is_relative_to(ROOT / "work"):
        parser.error("Report must be under this checkout's work directory")
    started = datetime.now(UTC)
    fingerprint = source_fingerprint()
    image = json.loads(
        command(["docker", "image", "inspect", "mockazure:identity"], "image-before")
    )[0]
    assert image["Config"]["Labels"]["org.mockazure.source-fingerprint"] == fingerprint, (
        "Rebuild M2 image"
    )
    container = command([*M2_COMPOSE, "ps", "-q", "api"], "api-before")
    assert (
        json.loads(command(["docker", "inspect", container], "container-before"))[0]["Image"]
        == image["Id"]
    )

    command(
        [
            sys.executable,
            "tools/prepare_clients.py",
            "--output",
            "work/vendor",
            "--manifest",
            args.client_manifest,
            "--verify",
        ],
        "artifact-hashes",
    )
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv must be available")
    wheel = next((ROOT / "dist").glob("mockazure-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        for source in (ROOT / "src/mockazure").rglob("*.py"):
            assert archive.read(source.relative_to(ROOT / "src").as_posix()) == source.read_bytes()
    directory = ROOT / "work" / ("identity-wheel-" + str(time.time_ns()))
    command([uv, "venv", "--python", sys.executable, directory], "wheel-venv")
    python = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    offline = {**os.environ, "UV_OFFLINE": "1"}
    command([uv, "pip", "install", "--python", python, wheel], "wheel-install", env=offline)
    command(
        [python, "-c", "import mockazure; assert 'site-packages' in mockazure.__file__"],
        "wheel-origin",
        env=offline,
    )
    state = ROOT / ".runtime/clients"
    config = json.loads((state / "powershell-state/client.json").read_text())
    replication = qualify_replicas(settings, python, config)
    probes = {}
    scripts = [
        ("python", "network_boundary.py", "network.json"),
        ("powershell", "Test-NetworkBoundary.ps1", "network.json"),
        ("python", "app_only.py", "app-only.json"),
        ("python", "delegated.py", "delegated.json"),
        ("powershell", "Test-AppOnlyConnection.ps1", "app-only.json"),
        ("powershell", "Test-DelegatedConnection.ps1", "delegated.json"),
        ("powershell", "Test-ExchangeBootstrap.ps1", "exchange-bootstrap.json"),
        ("powershell", "Test-ArmBootstrap.ps1", "arm-bootstrap.json"),
        ("powershell", "Test-CollectorConnections.ps1", "collectors.json"),
        ("powershell", "Test-DeniedConnections.ps1", "denied-connections.json"),
        ("powershell", "Test-ProfileSwitch.ps1", "profile-switch.json"),
    ]
    for language, script, output in scripts:
        if script == "Test-DelegatedConnection.ps1":
            for name in ("device-token.json", "authorization-code-token.json"):
                shutil.copyfile(state / "python-state" / name, state / "powershell-state" / name)
        before = time.time()
        command(
            [
                *CLIENT_COMPOSE,
                "run",
                "--rm",
                language,
                f"/project/integrations/{language}/{script}",
            ],
            script,
            timeout=180,
        )
        path = state / f"{language}-state" / output
        assert path.stat().st_mtime >= before - 2, "Stale client report"
        result = json.loads(path.read_text(encoding="utf-8-sig"))
        if output == "network.json":
            assert all(result[key] for key in ("dns_blocked", "ipv4_blocked", "ipv6_blocked"))
        else:
            assert result.get("passed", result.get("graph_passed", False))
        probes[f"{language}/{output}"] = result
    report = {
        "schema_version": 1,
        "qualified_at": datetime.now(UTC).isoformat(),
        "started_at": started.isoformat(),
        "source_commit": command(["git", "rev-parse", "HEAD"], "commit"),
        "source_fingerprint": source_fingerprint(),
        "wheel_sha256": sha(wheel),
        "manifest_sha256": sha(args.client_manifest),
        "lock_sha256": sha(ROOT / "uv.lock"),
        "runtime_image_id": command(
            ["docker", "image", "inspect", "mockazure:identity", "--format", "{{.Id}}"], "image"
        ),
        "expected_tenant_id": config["tenant_id"],
        "expected_subscription_id": config["subscription_id"],
        "replication": replication,
        "probes": probes,
        "limitations": [
            "PowerShell uses explicit local tokens",
            "Connection gates, not collection workflows",
            "M1 metadata export remains metadata-only",
        ],
    }
    report_path.write_text(json.dumps(report, indent=2))
    print(json.dumps({"passed": True, "report": str(report_path), "client_probes": len(probes)}))


if __name__ == "__main__":
    main()

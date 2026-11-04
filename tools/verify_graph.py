"""Qualify this checkout's isolated M3 deployment using real clients and persistent state."""

import argparse
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from mockazure.config import load_settings

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.qualification_profile import profile_for  # noqa: E402

PROFILE = profile_for("m3")
RUNTIME = ROOT / PROFILE.text(".runtime/m3")
COMPOSE = ["docker", "compose", "-f", PROFILE.text("deploy/compose/milestones/compose.m3.yaml")]
CLIENTS = [
    "docker",
    "compose",
    "-f",
    PROFILE.text("deploy/compose/milestones/compose.clients.m3.yaml"),
]


def sha(path):
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_fingerprint():
    import hashlib

    files = {
        ROOT / name
        for name in (
            "pyproject.toml",
            "uv.lock",
            "build-requirements.txt",
            "Dockerfile",
            "integrations/Dockerfile.python",
            PROFILE.text("deploy/compose/milestones/compose.m3.yaml"),
            PROFILE.text("deploy/compose/milestones/compose.clients.m3.yaml"),
            PROFILE.text("deploy/haproxy-m3.cfg"),
        )
    }
    from tools.source_inputs import qualification_files

    files.update(qualification_files(ROOT))
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def command(args, label, *, env=None, timeout=300):
    result = subprocess.run(
        [str(a) for a in args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout
    )
    (ROOT / "work" / f"graph-{label}.log").write_text(
        result.stdout + result.stderr, encoding="utf-8"
    )
    if result.returncode:
        raise RuntimeError(f"Qualification failed: {label}; inspect its project-local log")
    return result.stdout.strip()


def checked(response, status=200):
    assert response.status_code == status, (
        f"{response.request.method} {response.request.url.path}: "
        f"{response.status_code}, expected {status}"
    )
    return response.json() if response.content else None


def wait_ready(client, base):
    until = time.monotonic() + 60
    while time.monotonic() < until:
        try:
            if client.get(base + "/_mock/ready", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError("M3 readiness timed out")


def isolated_environment():
    environment = {k: v for k, v in os.environ.items() if not k.startswith("MOCKAZURE_")}
    environment["PYTHONPATH"] = str(ROOT / "tests/support/network_guard")
    environment["MOCKAZURE_TEST_NETWORK_LOG"] = str(ROOT / "work/graph-native-network.log")
    return environment


class Replica:
    def __init__(self, python, port):
        self.python, self.port = python, port
        self.base = f"https://localhost:{port}"
        self.config = RUNTIME / f"replica-{port}.json"
        self.values = json.loads((RUNTIME / "instance/native.json").read_text())
        self.values.update(
            listen_host="127.0.0.1",
            listen_port=port,
            data_listen_ports=[],
            admin_base_url=self.base,
            data_dir=str((RUNTIME / "instance").resolve()),
        )
        self.values["client_profiles"]["host"] = {
            name: self.base for name in self.values["client_profiles"]["host"]
        }
        self.process = self.log = None

    def start(self, client):
        self.config.write_text(json.dumps(self.values))
        self.log = (ROOT / f"work/graph-replica-{self.port}.log").open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            [str(self.python), "-m", "mockazure", "serve", "--config", str(self.config)],
            cwd=ROOT / "work",
            env=isolated_environment(),
            stdout=self.log,
            stderr=self.log,
        )
        wait_ready(client, self.base)
        assert self.process.poll() is None

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.log:
            self.log.close()


def local_url(base, url):
    parsed = urlsplit(url)
    assert parsed.hostname in {"localhost", "graph.mockazure.test"}
    return base + parsed.path + ("?" + parsed.query if parsed.query else "")


def user_body(config, prefix):
    suffix = secrets.token_hex(6)
    return {
        "displayName": "Qualification " + suffix,
        "mailNickname": prefix + suffix,
        "userPrincipalName": prefix + suffix + "@" + config["domain"],
        "accountEnabled": True,
        "passwordProfile": {
            "password": secrets.token_urlsafe(24),
            "forceChangePasswordNextSignIn": False,
        },
    }


def runtime_checks(client, config, a, b):
    state = RUNTIME / "clients/native-state"
    secret = json.loads((state / "secret.json").read_text())["secret"]
    token_path = f"/{config['tenant_id']}/oauth2/v2.0/token"
    fields = {
        "grant_type": "client_credentials",
        "client_id": config["client_id"],
        "client_secret": secret,
        "scope": "https://graph.microsoft.com/.default",
    }
    raw = checked(client.post(a.base + token_path, data=fields))["access_token"]
    headers = {"Authorization": "Bearer " + raw}
    admin = {"Authorization": "Bearer " + (RUNTIME / "instance/admin.token").read_text().strip()}
    group = checked(
        client.post(
            a.base + "/v1.0/groups",
            headers=headers,
            json={
                "displayName": "Replica qualification",
                "mailNickname": "replica",
                "mailEnabled": False,
                "securityEnabled": True,
                "groupTypes": [],
            },
        ),
        201,
    )["id"]
    temporary = added = None
    changed_id = config["seed_ids"][-1]
    changed_before = checked(client.get(a.base + "/v1.0/users/" + changed_id, headers=headers))[
        "displayName"
    ]
    try:
        body = user_body(config, "seed")
        temporary = checked(client.post(a.base + "/v1.0/users", headers=headers, json=body), 201)
        query = {
            "$filter": "startsWith(userPrincipalName,'seed') and id ne '"
            + config["seed_first_id"]
            + "'",
            "$select": "id,displayName",
            "$top": "333",
            "$count": "true",
        }
        advanced = {**headers, "ConsistencyLevel": "eventual"}
        first = checked(client.get(a.base + "/v1.0/users", params=query, headers=advanced))
        assert first["@odata.count"] == 5000
        saved = first["@odata.nextLink"]
        replay = checked(client.get(saved, headers=advanced))
        across = checked(client.get(local_url(b.base, saved), headers=advanced))
        assert across["value"] == replay["value"]
        assert (
            urlsplit(across["@odata.nextLink"]).query == urlsplit(replay["@odata.nextLink"]).query
        )
        expected = {
            row["id"]: row["displayName"]
            for row in json.loads((RUNTIME / "seed.json").read_text())["users"]
            if row["id"] != config["seed_first_id"]
        }
        expected[temporary["id"]] = body["displayName"]
        checked(
            client.patch(
                b.base + "/v1.0/users/" + changed_id,
                headers=headers,
                json={"displayName": "Concurrent mutation"},
            ),
            204,
        )
        checked(client.delete(b.base + "/v1.0/users/" + temporary["id"], headers=headers), 204)
        temporary = None
        added = checked(
            client.post(b.base + "/v1.0/users", headers=headers, json=user_body(config, "seed")),
            201,
        )["id"]
        observed = list(first["value"])
        url = saved
        pages = 1
        while url:
            page = checked(client.get(local_url(b.base, url), headers=advanced))
            observed.extend(page["value"])
            url = page.get("@odata.nextLink")
            pages += 1
            assert pages < 30
        assert [row["id"] for row in observed] == sorted(expected)
        assert {row["id"]: row["displayName"] for row in observed} == expected
        assert len(observed) == len({row["id"] for row in observed}) == 5000
        fresh = checked(client.get(b.base + "/v1.0/users/" + changed_id, headers=headers))
        assert fresh["displayName"] == "Concurrent mutation"
        initial = checked(client.get(a.base + "/v1.0/groups/delta", headers=headers))
        while "@odata.nextLink" in initial:
            initial = checked(client.get(initial["@odata.nextLink"], headers=headers))
        delta_link = initial["@odata.deltaLink"]
        a.stop()
        a.start(client)
        assert checked(client.get(saved, headers=advanced)) == replay
        command([*COMPOSE, "restart", "db"], "postgres-restart", timeout=90)
        wait_ready(client, a.base)
        wait_ready(client, b.base)
        assert checked(client.get(saved, headers=advanced)) == replay
        checked(
            client.patch(
                b.base + "/v1.0/groups/" + group,
                headers=headers,
                json={"displayName": "After PostgreSQL restart"},
            ),
            204,
        )
        delta = checked(client.get(local_url(b.base, delta_link), headers=headers))
        assert any(
            row["id"] == group and row["displayName"] == "After PostgreSQL restart"
            for row in delta["value"]
        )
        denied = client.post(
            a.base + f"/v1.0/groups/{group}/members/$ref",
            headers=headers,
            json={
                "@odata.id": a.base + "/v1.0/directoryObjects/" + config["foreign_user_id"],
            },
        )
        assert denied.status_code == 404
        wrong = checked(
            client.post(
                a.base + token_path,
                data={
                    **fields,
                    "scope": "https://outlook.office365.com/.default",
                },
            )
        )["access_token"]
        assert (
            client.get(
                a.base + "/v1.0/users",
                headers={
                    "Authorization": "Bearer " + wrong,
                },
            ).status_code
            == 401
        )
        checked(
            client.post(
                a.base + f"/_mock/tenants/{config['tenant_id']}/graph/faults",
                headers=admin,
                json={
                    "client_id": config["client_id"],
                    "resource": "groups",
                    "kind": "next_read_429",
                    "retry_after": 1,
                },
            ),
            201,
        )
        a.stop()
        a.start(client)
        fault = client.get(b.base + "/v1.0/groups", headers=headers)
        assert fault.status_code == 429 and fault.headers["Retry-After"] == "1"
        time.sleep(1)
        checked(client.get(a.base + "/v1.0/groups", headers=headers))
        return dict.fromkeys(
            (
                "snapshot_5000_concurrent_mutation",
                "two_api_replicas",
                "api_restart",
                "postgres_restart",
                "continuation_replay",
                "delta_resume",
                "cross_tenant_reference_denied",
                "wrong_audience_denied",
                "shared_429_recovery",
            ),
            True,
        )
    finally:
        checked(
            client.patch(
                a.base + "/v1.0/users/" + changed_id,
                headers=headers,
                json={"displayName": changed_before},
            ),
            204,
        )
        for identifier in (added, temporary["id"] if temporary else None):
            if identifier:
                checked(client.delete(a.base + "/v1.0/users/" + identifier, headers=headers), 204)
        checked(client.delete(a.base + "/v1.0/groups/" + group, headers=headers), 204)


def fresh_report(path, before):
    assert path.stat().st_mtime >= before - 2, "Stale qualification report"
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    output = args.report.resolve()
    assert output.is_relative_to(ROOT / "work")
    started = datetime.now(UTC)
    fingerprint = source_fingerprint()
    image = json.loads(
        command(["docker", "image", "inspect", PROFILE.text("mockazure:graph")], "image")
    )[0]
    assert image["Config"]["Labels"]["org.mockazure.source-fingerprint"] == fingerprint, (
        "Rebuild M3 image for the current source fingerprint"
    )
    container = command([*COMPOSE, "ps", "-q", "api"], "api")
    assert (
        json.loads(command(["docker", "inspect", container], "container"))[0]["Image"]
        == image["Id"]
    )
    network = json.loads(
        command(
            ["docker", "network", "inspect", PROFILE.text("mockazure-m3-clients-isolated")],
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
    wheel = next((ROOT / "dist").glob("mockazure-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        for source in (ROOT / "src/mockazure").rglob("*.py"):
            assert archive.read(source.relative_to(ROOT / "src").as_posix()) == source.read_bytes()
    directory = ROOT / "work" / ("graph-wheel-" + str(time.time_ns()))
    command([uv, "venv", "--python", sys.executable, directory], "wheel-venv")
    python = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    offline = {**os.environ, "UV_OFFLINE": "1"}
    command([uv, "pip", "install", "--python", python, wheel], "wheel-install", env=offline)
    command(
        [python, "-c", "import mockazure; assert 'site-packages' in mockazure.__file__"],
        "wheel-origin",
        env=offline,
    )
    state = RUNTIME / "clients"
    config = json.loads((state / "native-state/client.json").read_text())
    settings = load_settings(RUNTIME / "instance/native.json")
    checks = {"clean_wheel_install": True}
    clients = {}
    replicas = [Replica(python, port) for port in (PROFILE.port(48444), PROFILE.port(48445))]
    # Bind checks prevent accidentally treating an unrelated listener as our replica.
    for replica in replicas:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", replica.port))
    with httpx.Client(
        verify=ssl.create_default_context(cafile=str(settings.ca_file)),
        trust_env=False,
        timeout=30,
    ) as client:
        try:
            for replica in replicas:
                replica.start(client)
            checks.update(runtime_checks(client, config, *replicas))
            dataset_path = ROOT / "work" / ("graph-export-" + str(time.time_ns()) + ".json")
            command(
                [
                    python,
                    "-m",
                    "mockazure",
                    "directory",
                    "export",
                    "--tenant-id",
                    config["tenant_id"],
                    "--output",
                    dataset_path,
                    "--config",
                    replicas[0].config,
                ],
                "native-cli-export",
                env=isolated_environment(),
            )
            dataset = json.loads(dataset_path.read_text())
            assert len([u for u in dataset["users"] if u["id"] in config["seed_ids"]]) == 5000
            assert dataset["schema_version"] == 1
            checks["native_cli_dataset"] = True
            # The guard is also active for both API replicas and the SDK subprocess.
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
            checked(
                client.post(
                    replicas[0].base + f"/_mock/tenants/{config['tenant_id']}/graph/faults",
                    headers={"Authorization": "Bearer " + settings.token_file.read_text().strip()},
                    json={
                        "client_id": config["client_id"],
                        "resource": "users",
                        "kind": "next_read_429",
                        "retry_after": 1,
                    },
                ),
                201,
            )
            # The first generated SDK directory read must transparently retry this
            # persisted 429 using its standard middleware; no harness retry wraps it.
            before = time.time()
            command(
                [
                    sys.executable,
                    "integrations/python/graph_directory.py",
                    "--config",
                    state / "native-state/client.json",
                ],
                "native-sdk",
                env=isolated_environment(),
                timeout=600,
            )
            clients["native_python"] = fresh_report(
                state / "native-state/graph-directory.json", before
            )
        finally:
            for replica in replicas:
                replica.stop()
    for language, script in (
        ("python", "network_boundary.py"),
        ("powershell", "Test-NetworkBoundary.ps1"),
        ("python", "graph_directory.py"),
        ("powershell", "Test-GraphDirectory.ps1"),
    ):
        if script == "Test-GraphDirectory.ps1":
            for label in ("application", "delegated", "denied_application", "denied_delegated"):
                name = label + "-token.json"
                shutil.copyfile(state / "python-state" / name, state / "powershell-state" / name)
        before = time.time()
        command(
            [
                *CLIENTS,
                "run",
                "--rm",
                language,
                f"/project/integrations/{language}/{script}",
            ],
            script,
            timeout=600,
        )
        network_probe = "NetworkBoundary" in script or script == "network_boundary.py"
        path = (
            state
            / f"{language}-state"
            / ("network.json" if network_probe else "graph-directory.json")
        )
        result = fresh_report(path, before)
        if network_probe:
            assert all(result[key] for key in ("dns_blocked", "ipv4_blocked", "ipv6_blocked"))
            checks[f"container_{language}_network_blocked"] = True
        else:
            assert result["passed"]
            clients[f"container_{language}"] = result
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
        "seed_first_id": config["seed_first_id"],
        "checks": checks,
        "clients": clients,
        "limitations": [
            "PowerShell uses explicit local tokens; Python uses actual MSAL flows",
            "PowerShell opaque delta traversal uses Invoke-MgGraphRequest",
            "5,000-user functional qualification is not a throughput SLA",
            "Evidence outbox pending M3B; no audit/evidence or collector collection claims",
            "M3C security administration, broader Exchange and ARM remain later milestones",
        ],
    }
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "checks": len(checks), "clients": len(clients)}))


if __name__ == "__main__":
    main()

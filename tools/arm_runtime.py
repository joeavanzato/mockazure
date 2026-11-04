"""Local M5 runtime probes; no upstream network access."""

import base64
import hashlib
import html
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.qualification_profile import profile_for  # noqa: E402

PROFILE = profile_for("m5")
RUNTIME = ROOT / PROFILE.text(".runtime/m5")
COMPOSE = ["docker", "compose", "-f", PROFILE.text("deploy/compose/milestones/compose.m5.yaml")]
CLIENTS = [
    "docker",
    "compose",
    "-f",
    PROFILE.text("deploy/compose/milestones/compose.clients.m5.yaml"),
]


def command(args, label, *, env=None, timeout=300):
    result = subprocess.run(
        [str(a) for a in args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    (ROOT / "work" / f"arm-{label}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
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
    raise RuntimeError("M5 readiness timed out")


def isolated_environment():
    environment = {k: v for k, v in os.environ.items() if not k.startswith("MOCKAZURE_")}
    environment["PYTHONPATH"] = str(ROOT / "tests/support/network_guard")
    environment["MOCKAZURE_TEST_NETWORK_LOG"] = str(ROOT / "work/arm-native-network.log")
    return environment


class Replica:
    def __init__(self, python, port):
        self.python, self.port = python, port
        self.base = f"https://localhost:{port}"
        self.config = RUNTIME / f"replica-{port}.json"
        self.values = json.loads((RUNTIME / "instance/native.json").read_text(encoding="utf-8"))
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
        self.config.write_text(json.dumps(self.values), encoding="utf-8")
        self.log = (ROOT / f"work/arm-replica-{self.port}.log").open("a", encoding="utf-8")
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
    assert parsed.hostname in {"localhost", "management.mockazure.test"}
    return base + parsed.path + ("?" + parsed.query if parsed.query else "")


def browser(client, base, config, *, mfa="skip", scopes="User.Read offline_access", user=None):
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    response = client.get(
        base + f"/{config['tenant_id']}/oauth2/v2.0/authorize",
        params={
            "client_id": config["public_client_id"],
            "redirect_uri": "http://localhost:8123/callback",
            "response_type": "code",
            "scope": scopes,
            "state": secrets.token_urlsafe(12),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert response.status_code == 200
    fields = {
        name: html.unescape(re.search(r'name="' + name + r'" value="([^"]*)"', response.text)[1])
        for name in ("transaction", "csrf")
    }
    user = user or json.loads(Path(config["user_file"]).read_text(encoding="utf-8"))
    for path, values in (
        ("/login", user),
        ("/consent", {"decision": "approve"}),
        ("/mfa", {"decision": mfa}),
    ):
        if path == "/mfa":
            from integrations.python.mfa_form import mfa_form_values

            values = mfa_form_values(response.text, values)
        response = client.post(base + path, data=fields | values)
        if response.status_code != 200:
            break
    return response, verifier


def redeem(client, base, config, callback):
    response, verifier = callback
    assert response.status_code == 303
    code = parse_qs(urlsplit(response.headers["location"]).query)["code"][0]
    return client.post(
        base + f"/{config['tenant_id']}/oauth2/v2.0/token",
        data={
            "grant_type": "authorization_code",
            "client_id": config["public_client_id"],
            "redirect_uri": "http://localhost:8123/callback",
            "code": code,
            "code_verifier": verifier,
        },
    )


def auth_headers(client, base, config):
    raw = checked(
        client.post(
            base + f"/{config['tenant_id']}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": config["client_id"],
                "client_secret": json.loads(
                    Path(config["secret_file"]).read_text(encoding="utf-8")
                )["secret"],
                "scope": "https://management.azure.com/.default",
            },
        )
    )["access_token"]
    return {"Authorization": "Bearer " + raw}


def await_query(client, base, item, headers):
    for _ in range(240):
        row = checked(client.get(base + item, headers=headers))
        if row["status"] == "succeeded":
            return row
        assert row["status"] in {"notStarted", "running"}
        time.sleep(0.25)
    raise AssertionError("Local audit query timed out")


def runtime_checks(client, config, a, b):
    from uuid import uuid4

    headers = auth_headers(client, a.base, config)
    admin = {
        "Authorization": "Bearer "
        + (RUNTIME / "instance/admin.token").read_text(encoding="utf-8").strip()
    }
    sub = config["subscription_id"]
    control = f"/_mock/tenants/{config['tenant_id']}/subscriptions/{sub}"
    scope = f"/subscriptions/{sub}"
    args = {"api-version": "2021-04-01"}
    activity = scope + "/providers/Microsoft.Insights/eventtypes/management/values"
    fixture = config["arm_fixture"]
    query = {
        "api-version": "2015-04-01",
        "$filter": (
            f"eventTimestamp ge '{fixture['start']}' and eventTimestamp le '{fixture['end']}'"
        ),
    }
    first = checked(client.get(a.base + activity, params=query, headers=headers))
    saved = first["nextLink"]
    reference = checked(client.get(local_url(b.base, saved), headers=headers))
    assert (
        reference["value"]
        == checked(client.get(local_url(a.base, saved), headers=headers))["value"]
    )
    checks = {"two_api_replicas": True}
    name = "runtime-" + uuid4().hex[:10]
    group = scope + "/resourceGroups/" + name
    checked(
        client.put(
            a.base + group,
            params=args,
            headers=headers,
            json={"location": "eastus", "tags": {"Runtime": "restart"}},
        ),
        201,
    )
    command([*COMPOSE, "stop", "worker", "worker2"], "workers-stop", timeout=60)
    try:
        response = client.delete(a.base + group, params=args, headers=headers)
        assert response.status_code == 202
        operation = response.headers["Location"]
        assert client.get(local_url(b.base, operation), headers=headers).status_code == 202
        a.stop()
        a.start(client)
        assert client.get(local_url(a.base, operation), headers=headers).status_code == 202
        checks["api_restart"] = True
        command([*COMPOSE, "restart", "db"], "postgres-restart", timeout=90)
        wait_ready(client, a.base)
        wait_ready(client, b.base)
        assert client.get(local_url(b.base, operation), headers=headers).status_code == 202
        checks["postgres_restart"] = checks["delete_pending_restart"] = True
    finally:
        command([*COMPOSE, "up", "-d", "worker", "worker2"], "workers-start", timeout=90)
    for _ in range(120):
        response = client.get(local_url(b.base, operation), headers=headers)
        if response.status_code == 200:
            break
        assert response.status_code == 202
        time.sleep(0.5)
    assert response.status_code == 200
    assert client.get(a.base + group, params=args, headers=headers).status_code == 404
    checks["worker_restart"] = True
    for service in ("worker", "worker2"):
        identifier = command([*COMPOSE, "ps", "-q", service], service)
        info = json.loads(command(["docker", "inspect", identifier], service + "-state"))[0]
        assert info["State"]["Running"] and "worker" in info["Config"]["Cmd"]
    checks["two_workers"] = True
    b.stop()
    b.start(client)
    assert (
        checked(client.get(local_url(b.base, saved), headers=headers))["value"]
        == reference["value"]
    )
    rows, link = list(first["value"]), saved
    while link:
        page = checked(client.get(local_url(b.base, link), headers=headers))
        rows.extend(page["value"])
        link = page.get("nextLink")
        assert len(rows) <= 1003
    expected = json.loads((RUNTIME / "arm-expected.json").read_text(encoding="utf-8"))
    assert len(rows) == 1003 and {r["eventDataId"] for r in rows} == {
        r["eventDataId"] for r in expected
    }
    checks["activity_snapshot_resume"] = True
    foreign = f"/subscriptions/{config['foreign_subscription_id']}/resources"
    assert client.get(a.base + foreign, params=args, headers=headers).status_code == 404
    checks["tenant_isolation"] = True
    delegated = checked(
        redeem(
            client,
            a.base,
            config,
            browser(
                client,
                a.base,
                config,
                scopes="https://management.azure.com/user_impersonation offline_access",
                mfa="complete",
            ),
        )
    )
    delegated_headers = {"Authorization": "Bearer " + delegated["access_token"]}
    delegated_page = checked(client.get(a.base + activity, params=query, headers=delegated_headers))
    assignment = control + "/role-assignments/" + config["assignments"]["delegated"]
    try:
        checked(client.delete(a.base + assignment, headers=admin), 200)
        assert (
            client.get(b.base + activity, params=query, headers=delegated_headers).status_code
            == 403
        )
        assert (
            client.get(
                local_url(b.base, delegated_page["nextLink"]), headers=delegated_headers
            ).status_code
            == 403
        )
        checks["scope_denied"] = checks["page_reauthorization"] = True
    finally:
        checked(
            client.put(
                a.base + assignment,
                headers=admin,
                json={
                    "principal_id": config["user_id"],
                    "principal_type": "User",
                    "scope": scope,
                    "role": "Owner",
                },
            )
        )
    native_query = {
        "api-version": "2015-04-01",
        "$filter": f"eventTimestamp ge '2026-10-08T00:00:00Z' and resourceGroupName eq '{name}'",
    }
    for _ in range(120):
        native = checked(client.get(a.base + activity, params=native_query, headers=headers))[
            "value"
        ]
        operations = {r["operationName"]["value"].lower() for r in native}
        if {
            "microsoft.resources/subscriptions/resourcegroups/write",
            "microsoft.resources/subscriptions/resourcegroups/delete",
        } <= operations:
            break
        time.sleep(0.5)
    assert len(native) == 2 and all(r["status"]["value"] == "Succeeded" for r in native)
    (RUNTIME / "runtime-native-expected.json").write_text(
        json.dumps(native, indent=2), encoding="utf-8"
    )
    checks["native_evidence"] = True
    fault = {"client_id": config["client_id"], "kind": "next_page_429", "retry_after": 1}
    for status in (429, 503):
        checked(
            client.post(
                a.base + control + "/activity-faults",
                headers=admin,
                json={**fault, "kind": f"next_page_{status}"},
            ),
            201,
        )
        denied = client.get(local_url(a.base, saved), headers=headers)
        assert denied.status_code == status and denied.headers["Retry-After"] == "1"
        time.sleep(1)
        assert (
            checked(client.get(local_url(b.base, saved), headers=headers))["value"]
            == reference["value"]
        )
    checks["fault_retry"] = True
    # Leave one fault for the next real Monitor SDK request, which must retry normally.
    checked(
        client.post(
            a.base + control + "/activity-faults",
            headers=admin,
            json={**fault, "kind": "next_read_429"},
        ),
        201,
    )
    return checks


def capture_native_activity(config):
    import ssl

    base = PROFILE.text("https://localhost:36443")
    for _ in range(120):
        pending = command(
            [
                *COMPOSE,
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                PROFILE.text("mockazure_m5"),
                "-d",
                PROFILE.text("mockazure_m5"),
                "-tAc",
                "SELECT count(*) FROM jobs WHERE state IN ('pending','running')",
            ],
            "pending-publication",
        )
        if pending.strip() == "0":
            break
        time.sleep(0.5)
    assert pending.strip() == "0", "Pending evidence publication did not drain"
    with httpx.Client(
        verify=ssl.create_default_context(cafile=str(RUNTIME / "instance/ca.pem")),
        trust_env=False,
        follow_redirects=False,
        timeout=60,
    ) as client:
        headers = auth_headers(client, base, config)
        path = (
            f"/subscriptions/{config['subscription_id']}/providers/"
            "Microsoft.Insights/eventtypes/management/values"
        )
        page = checked(
            client.get(
                base + path,
                headers=headers,
                params={
                    "api-version": "2015-04-01",
                    "$filter": (
                        "eventTimestamp ge '2026-10-08T00:00:00Z' and "
                        "eventTimestamp le '2026-10-08T23:59:59Z'"
                    ),
                },
            )
        )
        rows = list(page["value"])
        while link := page.get("nextLink"):
            page = checked(client.get(local_url(base, link), headers=headers))
            rows.extend(page["value"])
    required = json.loads((RUNTIME / "runtime-native-expected.json").read_text(encoding="utf-8"))
    indexed = {r["eventDataId"]: r for r in rows}
    assert len(required) == 2 and all(indexed.get(r["eventDataId"]) == r for r in required)
    assert len(rows) == len(indexed)
    (RUNTIME / "arm-native-expected.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return rows

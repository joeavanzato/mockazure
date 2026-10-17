"""Local M4 runtime probes; no upstream network access."""

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

PROFILE = profile_for("m4")
RUNTIME = ROOT / PROFILE.text(".runtime/m4")
COMPOSE = ["docker", "compose", "-f", PROFILE.text("compose.m4.yaml")]
CLIENTS = ["docker", "compose", "-f", PROFILE.text("compose.clients.m4.yaml")]


def command(args, label, *, env=None, timeout=300):
    result = subprocess.run(
        [str(a) for a in args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout
    )
    (ROOT / "work" / f"mail-{label}.log").write_text(
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
    raise RuntimeError("M4 readiness timed out")


def isolated_environment():
    environment = {k: v for k, v in os.environ.items() if not k.startswith("MOCKAZURE_")}
    environment["PYTHONPATH"] = str(ROOT / "tests/support/network_guard")
    environment["MOCKAZURE_TEST_NETWORK_LOG"] = str(ROOT / "work/mail-native-network.log")
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
            admin_base_url=self.base,
            data_dir=str((RUNTIME / "instance").resolve()),
        )
        self.values["client_profiles"]["host"] = {
            name: self.base for name in self.values["client_profiles"]["host"]
        }
        self.process = self.log = None

    def start(self, client):
        self.config.write_text(json.dumps(self.values))
        self.log = (ROOT / f"work/mail-replica-{self.port}.log").open("a", encoding="utf-8")
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
    user = user or json.loads(Path(config["user_file"]).read_text())
    for path, values in (
        ("/login", user),
        ("/consent", {"decision": "approve"}),
        ("/mfa", {"decision": mfa}),
    ):
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
                "client_secret": json.loads(Path(config["secret_file"]).read_text())["secret"],
                "scope": "https://graph.microsoft.com/.default",
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


def await_fixture_publication(client, base, headers, body, expected_ids, *, timeout=300):
    """Wait through public Graph queries; each query pins an immutable publication revision."""
    deadline = time.monotonic() + timeout
    query_path = "/v1.0/security/auditLog/queries"
    while time.monotonic() < deadline:
        query = checked(client.post(base + query_path, headers=headers, json=body), 201)
        item = query_path + "/" + query["id"]
        await_query(client, base, item, headers)
        link, observed = base + item + "/records", set()
        while link:
            page = checked(client.get(link, headers=headers))
            for row in page["value"]:
                assert row["id"] in expected_ids, "Unexpected audit fixture identity"
                assert row["id"] not in observed, "Duplicate audit fixture identity"
                observed.add(row["id"])
            link = page.get("@odata.nextLink")
            if link:
                link = local_url(base, link)
        if observed == expected_ids:
            return
        time.sleep(0.5)
    raise AssertionError("Audit fixture publication timed out")


def runtime_checks(client, config, a, b):
    from datetime import UTC, datetime, timedelta

    headers = auth_headers(client, a.base, config)
    admin = {"Authorization": "Bearer " + (RUNTIME / "instance/admin.token").read_text().strip()}
    tenant = config["tenant_id"]
    control = f"/_mock/tenants/{tenant}"
    query_path = "/v1.0/security/auditLog/queries"
    fixture = config["mail_fixture"]
    body = {
        "displayName": "Runtime persisted audit",
        "serviceFilters": ["Exchange"],
        "operationFilters": ["MailItemsAccessed"],
        "filterStartDateTime": fixture["audit_start"],
        "filterEndDateTime": fixture["audit_end"],
    }
    # Import acknowledgement is not publication. Never stop workers or pin the
    # restart-test snapshot until the full fixture is visible through Graph.
    await_fixture_publication(client, a.base, headers, body, set(fixture["audit_ids"]))
    checks = {}
    command([*COMPOSE, "stop", "worker", "worker2"], "workers-stop", timeout=60)
    try:
        created = checked(client.post(a.base + query_path, headers=headers, json=body), 201)
        item = query_path + "/" + created["id"]
        assert created["status"] == "notStarted"
        assert client.get(b.base + item + "/records", headers=headers).status_code == 409
        a.stop()
        a.start(client)
        assert checked(client.get(a.base + item, headers=headers))["status"] == "notStarted"
        checks["api_restart"] = True
        command([*COMPOSE, "restart", "db"], "postgres-restart", timeout=90)
        wait_ready(client, a.base)
        wait_ready(client, b.base)
        assert checked(client.get(b.base + item, headers=headers))["status"] == "notStarted"
        checks["postgres_restart"] = True
        checks["audit_pending_restart"] = True
    finally:
        command([*COMPOSE, "up", "-d", "worker", "worker2"], "workers-start", timeout=90)
    await_query(client, b.base, item, headers)
    checks["worker_restart"] = True
    for name in ("worker", "worker2"):
        identifier = command([*COMPOSE, "ps", "-q", name], name)
        state = json.loads(command(["docker", "inspect", identifier], name + "-state"))[0]
        assert state["State"]["Running"] and "worker" in state["Config"]["Cmd"]
    checks["two_workers"] = True
    first = checked(client.get(a.base + item + "/records", headers=headers))
    saved = first["@odata.nextLink"]
    page = checked(client.get(local_url(b.base, saved), headers=headers))
    assert checked(client.get(local_url(a.base, saved), headers=headers))["value"] == page["value"]
    checks["two_api_replicas"] = True
    b.stop()
    b.start(client)
    assert checked(client.get(local_url(b.base, saved), headers=headers)) == page
    records = list(first["value"])
    link = saved
    while link:
        current = checked(client.get(local_url(b.base, link), headers=headers))
        records.extend(current["value"])
        link = current.get("@odata.nextLink")
        assert len(records) <= 1003
    assert len(records) == 1003 and {r["id"] for r in records} == set(fixture["audit_ids"])
    checks["audit_snapshot_resume"] = True
    rules = f"/v1.0/users/{config['user_id']}/mailFolders/inbox/messageRules"
    first = checked(client.get(a.base + rules + "?$top=1", headers=headers))
    saved_rules = first["@odata.nextLink"]
    before_page = checked(client.get(local_url(b.base, saved_rules), headers=headers))
    started = datetime.now(UTC) - timedelta(seconds=1)
    new_rule = checked(
        client.post(
            b.base + rules,
            headers=headers,
            json={
                "displayName": "Runtime temporary rule",
                "sequence": 0,
                "isEnabled": True,
                "actions": {"delete": True},
            },
        ),
        201,
    )
    try:
        after_page = checked(client.get(local_url(a.base, saved_rules), headers=headers))
        assert after_page["value"] == before_page["value"]
        assert local_url(a.base, after_page["@odata.nextLink"]) == local_url(
            a.base, before_page["@odata.nextLink"]
        )
    finally:
        checked(client.delete(a.base + rules + "/" + new_rule["id"], headers=headers), 204)
    checks["rule_snapshot_resume"] = True
    other = f"/v1.0/users/{config['foreign_user_id']}/mailFolders/inbox/messageRules"
    assert client.get(a.base + other, headers=headers).status_code == 404
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
                scopes="AuditLogsQuery.Read.All offline_access",
                mfa="complete",
            ),
        )
    )
    delegated_headers = {"Authorization": "Bearer " + delegated["access_token"]}
    delegated_page = checked(
        client.get(a.base + item + "/records?$top=1", headers=delegated_headers)
    )
    role = control + f"/mail/audit-roles/{config['user_id']}/View-Only Audit Logs"
    checked(client.delete(a.base + role, headers=admin), 204)
    try:
        assert client.get(b.base + item, headers=delegated_headers).status_code == 403
        assert (
            client.get(
                local_url(b.base, delegated_page["@odata.nextLink"]), headers=delegated_headers
            ).status_code
            == 403
        )
        checks["role_denied"] = checks["page_reauthorization"] = True
    finally:
        checked(client.put(a.base + role, headers=admin))
    checked(
        client.put(
            a.base + control + "/mail/settings",
            headers=admin,
            json={"exchange_enabled": True, "audit_tier": "none"},
        )
    )
    try:
        assert client.get(b.base + item, headers=headers).status_code == 403
        checks["licence_denied"] = True
    finally:
        checked(
            client.put(
                a.base + control + "/mail/settings",
                headers=admin,
                json={"exchange_enabled": True, "audit_tier": "standard"},
            )
        )
    for _ in range(30):
        search = checked(
            client.post(
                a.base + query_path,
                headers=headers,
                json={
                    "displayName": "Runtime native projection",
                    "operationFilters": ["New-InboxRule", "Remove-InboxRule"],
                    "objectIdFilters": [config["mailbox_upn"] + "\\" + new_rule["id"]],
                    "filterStartDateTime": started.isoformat(),
                    "filterEndDateTime": datetime.now(UTC).isoformat(),
                },
            ),
            201,
        )
        native = query_path + "/" + search["id"]
        await_query(client, a.base, native, headers)
        evidence = checked(client.get(a.base + native + "/records", headers=headers))["value"]
        if len(evidence) == 2:
            break
        time.sleep(0.5)
    assert {r["operation"] for r in evidence} == {"New-InboxRule", "Remove-InboxRule"}
    checks["native_evidence"] = True
    checked(
        client.post(
            a.base + control + "/graph/faults",
            headers=admin,
            json={
                "client_id": config["client_id"],
                "resource": "users",
                "kind": "next_page_429",
                "retry_after": 1,
            },
        ),
        201,
    )
    throttled = client.get(local_url(a.base, saved_rules), headers=headers)
    assert throttled.status_code == 429 and throttled.headers["Retry-After"] == "1"
    time.sleep(1)
    assert checked(client.get(local_url(b.base, saved_rules), headers=headers)) == before_page
    checks["fault_retry"] = True
    # The next real generated SDK GET must traverse its unchanged RetryHandler.
    checked(
        client.post(
            a.base + control + "/graph/faults",
            headers=admin,
            json={
                "client_id": config["client_id"],
                "resource": "users",
                "kind": "next_read_429",
                "retry_after": 1,
            },
        ),
        201,
    )
    return checks

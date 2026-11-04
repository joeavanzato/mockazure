"""Local M3C runtime probes; no upstream network access."""

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
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.qualification_profile import profile_for  # noqa: E402

PROFILE = profile_for("m3c")
RUNTIME = ROOT / PROFILE.text(".runtime/m3c")
COMPOSE = ["docker", "compose", "-f", PROFILE.text("deploy/compose/milestones/compose.m3c.yaml")]
CLIENTS = [
    "docker",
    "compose",
    "-f",
    PROFILE.text("deploy/compose/milestones/compose.clients.m3c.yaml"),
]


def command(args, label, *, env=None, timeout=300):
    result = subprocess.run(
        [str(a) for a in args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout
    )
    (ROOT / "work" / f"security-{label}.log").write_text(
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
    raise RuntimeError("M3C readiness timed out")


def isolated_environment():
    environment = {k: v for k, v in os.environ.items() if not k.startswith("MOCKAZURE_")}
    environment["PYTHONPATH"] = str(ROOT / "tests/support/network_guard")
    environment["MOCKAZURE_TEST_NETWORK_LOG"] = str(ROOT / "work/security-native-network.log")
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
        self.log = (ROOT / f"work/security-replica-{self.port}.log").open("a", encoding="utf-8")
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
                "client_secret": json.loads(Path(config["secret_file"]).read_text())["secret"],
                "scope": "https://graph.microsoft.com/.default",
            },
        )
    )["access_token"]
    return {"Authorization": "Bearer " + raw}


def runtime_checks(client, config, a, b):
    headers = auth_headers(client, a.base, config)
    admin = {"Authorization": "Bearer " + (RUNTIME / "instance/admin.token").read_text().strip()}
    checks = {}
    report = "/v1.0/reports/authenticationMethods/userRegistrationDetails"
    first = checked(
        client.get(
            a.base + report,
            headers=headers,
            params={"$filter": "startsWith(userPrincipalName,'seed')"},
        )
    )
    saved = first["@odata.nextLink"]
    replay = checked(client.get(local_url(a.base, saved), headers=headers))
    assert (
        checked(client.get(local_url(b.base, saved), headers=headers))["value"] == replay["value"]
    )
    checks["two_api_replicas"] = True
    changed = config["seed_ids"][-1]
    checked(
        client.patch(
            b.base + "/v1.0/users/" + changed, headers=headers, json={"accountEnabled": False}
        ),
        204,
    )
    try:
        a.stop()
        a.start(client)
        assert checked(client.get(local_url(a.base, saved), headers=headers)) == replay
        checks["api_restart"] = True
        command([*COMPOSE, "restart", "db"], "postgres-restart", timeout=90)
        wait_ready(client, a.base)
        wait_ready(client, b.base)
        assert (
            checked(client.get(local_url(b.base, saved), headers=headers))["value"]
            == replay["value"]
        )
        checks["postgres_restart"] = True
        values = list(first["value"])
        link = saved
        while link:
            page = checked(client.get(local_url(b.base, link), headers=headers))
            values.extend(page["value"])
            link = page.get("@odata.nextLink")
            assert len(values) <= 5000
        assert len(values) == 5000 and {v["id"] for v in values} == set(config["seed_ids"])
        assert client.get(b.base + report + "/" + changed, headers=headers).status_code == 404
        checks["security_snapshot_resume"] = True
    finally:
        checked(
            client.patch(
                a.base + "/v1.0/users/" + changed, headers=headers, json={"accountEnabled": True}
            ),
            204,
        )
    assert (
        client.get(
            b.base + "/v1.0/users/" + config["foreign_user_id"] + "/authentication/methods",
            headers=headers,
        ).status_code
        == 404
    )
    checks["tenant_isolation"] = True
    command([*COMPOSE, "restart", "worker", "worker2"], "workers-restart", timeout=90)
    wait_ready(client, a.base)
    for name in ("worker", "worker2"):
        identifier = command([*COMPOSE, "ps", "-q", name], name)
        state = json.loads(command(["docker", "inspect", identifier], name + "-state"))[0]
        assert state["State"]["Running"] and "worker" in state["Config"]["Cmd"]
    checks["two_workers"] = True
    checks.update(policy_checks(client, config, a.base, headers, admin))
    checks.update(role_grant_checks(client, config, a.base, headers, admin))
    return checks


def policy_checks(client, config, base, headers, admin):
    checks = {}
    graph = base + "/v1.0"
    body = {
        "displayName": "Runtime report-only policy",
        "state": "enabled",
        "conditions": {
            "users": {"includeUsers": [config["user_id"]]},
            "applications": {"includeApplications": ["All"]},
            "clientAppTypes": ["all"],
        },
        "grantControls": {"operator": "OR", "builtInControls": ["block"]},
    }
    policy = checked(
        client.post(graph + "/identity/conditionalAccess/policies", headers=headers, json=body), 201
    )
    path = graph + "/identity/conditionalAccess/policies/" + policy["id"]
    try:
        blocked, _ = browser(client, base, config)
        assert blocked.status_code == 400 and blocked.json()["error"] == "access_denied"
        assert "access_token" not in blocked.json()
        checks["policy_block"] = True
        checked(
            client.patch(
                path,
                headers=headers,
                json={"grantControls": {"operator": "OR", "builtInControls": ["mfa"]}},
            ),
            204,
        )
        unmet, _ = browser(client, base, config)
        assert unmet.status_code == 400
        success = checked(
            redeem(client, base, config, browser(client, base, config, mfa="complete"))
        )
        assert success["access_token"]
        checks["policy_mfa"] = True
        checked(
            client.patch(
                path,
                headers=headers,
                json={
                    "state": "enabledForReportingButNotEnforced",
                    "grantControls": {"operator": "OR", "builtInControls": ["block"]},
                },
            ),
            204,
        )
        issued = checked(redeem(client, base, config, browser(client, base, config)))
        assert issued["access_token"]
        checks["policy_report_only"] = True
        checked(client.patch(path, headers=headers, json={"state": "disabled"}), 204)
        callback = browser(client, base, config)
        assert callback[0].status_code == 303
        checked(
            client.patch(
                path,
                headers=headers,
                json={"state": "enabled", "displayName": "Renamed enforced policy"},
            ),
            204,
        )
        pending = redeem(client, base, config, callback)
        assert pending.status_code == 400 and "access_token" not in pending.json()
        refresh = client.post(
            base + f"/{config['tenant_id']}/oauth2/v2.0/token",
            data={
                "grant_type": "refresh_token",
                "client_id": config["public_client_id"],
                "refresh_token": issued["refresh_token"],
            },
        )
        assert refresh.status_code == 400 and "access_token" not in refresh.json()
        checks["policy_pending_flow"] = True
        # Workers project actual immutable authentication facts, not imported expected records.
        detailed = {**headers, "Prefer": "include-unknown-enum-members"}
        until = time.monotonic() + 60
        while True:
            page = checked(
                client.get(
                    graph + "/auditLogs/signIns",
                    headers=detailed,
                    params={"$filter": "userId eq '" + config["user_id"] + "'"},
                )
            )
            values = list(page["value"])
            while page.get("@odata.nextLink"):
                page = checked(
                    client.get(local_url(base, page["@odata.nextLink"]), headers=detailed)
                )
                values.extend(page["value"])
            policy_records = [
                (row, p)
                for row in values
                for p in row.get("appliedConditionalAccessPolicies", [])
                if p["id"] == policy["id"]
            ]
            results = {p["result"] for _, p in policy_records}
            if {"failure", "success", "reportOnlyFailure"} <= results:
                break
            assert time.monotonic() < until, "Workers did not publish policy evidence"
            time.sleep(0.5)
        original = next((row, p) for row, p in policy_records if p["result"] == "reportOnlyFailure")
        assert original[0]["status"]["errorCode"] == 0
        assert original[1]["displayName"] == body["displayName"]
        assert any(row["status"]["errorCode"] == 53003 for row, _ in policy_records)
        checks["policy_evidence"] = True
        # A separate app has audit access but no policy disclosure permission.
        identity = base + f"/_mock/tenants/{config['tenant_id']}/identity"
        app = checked(
            client.post(
                identity + "/applications",
                headers=admin,
                json={
                    "display_name": "Disclosure probe",
                    "client_mode": "confidential",
                    "required_permissions": [
                        {
                            "resource": "graph",
                            "kind": "application",
                            "permissions": ["AuditLog.Read.All"],
                        }
                    ],
                },
            ),
            201,
        )
        secret = checked(
            client.post(
                identity + "/applications/" + app["client_id"] + "/credentials",
                headers=admin,
                json={"kind": "secret"},
            ),
            201,
        )
        checked(
            client.put(
                identity + "/grants/" + str(uuid4()),
                headers=admin,
                json={
                    "client_id": app["client_id"],
                    "kind": "application",
                    "resource": "graph",
                    "permissions": ["AuditLog.Read.All"],
                },
            )
        )
        token = checked(
            client.post(
                base + f"/{config['tenant_id']}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": app["client_id"],
                    "client_secret": secret["secret"],
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
        )["access_token"]
        hidden = checked(
            client.get(
                graph + "/auditLogs/signIns/" + original[0]["id"],
                headers={"Authorization": "Bearer " + token},
            )
        )
        assert "appliedConditionalAccessPolicies" not in hidden
        checks["policy_disclosure"] = True
        checked(client.delete(identity + "/credentials/" + secret["id"], headers=admin))
    finally:
        checked(client.delete(path, headers=headers), 204)
    return checks


def role_grant_checks(client, config, base, headers, admin):
    identity = base + f"/_mock/tenants/{config['tenant_id']}/identity"
    password = secrets.token_urlsafe(24)
    user = checked(
        client.post(
            identity + "/users",
            headers=admin,
            json={
                "upn": "role-probe-" + secrets.token_hex(6) + "@graph.test",
                "display_name": "Runtime role probe",
                "password": password,
            },
        ),
        201,
    )
    grant_id = str(uuid4())
    grant_path = identity + "/grants/" + grant_id
    grant = {
        "client_id": config["public_client_id"],
        "user_id": user["id"],
        "kind": "delegated",
        "resource": "graph",
        "permissions": ["User.Read", "AuditLog.Read.All"],
    }
    checked(client.put(grant_path, headers=admin, json=grant))
    role_path = identity + "/users/" + user["id"] + "/roles/Reports Reader"
    try:
        tokens = checked(
            redeem(
                client,
                base,
                config,
                browser(
                    client,
                    base,
                    config,
                    scopes="User.Read AuditLog.Read.All",
                    user={"username": user["upn"], "password": password},
                ),
            )
        )
        auth = {"Authorization": "Bearer " + tokens["access_token"]}
        report = (
            base
            + "/v1.0/reports/authenticationMethods/userRegistrationDetails/"
            + config["user_id"]
        )
        assert client.get(report, headers=auth).status_code == 403
        checked(client.put(role_path, headers=admin, json={}))
        checked(client.get(report, headers=auth))
        checked(client.delete(role_path, headers=admin))
        assert client.get(report, headers=auth).status_code == 403
        checked(client.put(role_path, headers=admin, json={}))
        checked(client.get(report, headers=auth))
        checked(client.put(grant_path, headers=admin, json={**grant, "permissions": ["User.Read"]}))
        # Issued access-token claims retain their documented lifetime semantics.
        checked(client.get(report, headers=auth))
        fresh = checked(
            redeem(
                client,
                base,
                config,
                browser(
                    client,
                    base,
                    config,
                    scopes="User.Read",
                    user={"username": user["upn"], "password": password},
                ),
            )
        )
        assert (
            client.get(
                report, headers={"Authorization": "Bearer " + fresh["access_token"]}
            ).status_code
            == 403
        )
        rejected, _ = browser(
            client,
            base,
            config,
            scopes="User.Read AuditLog.Read.All",
            user={"username": user["upn"], "password": password},
        )
        assert rejected.status_code == 400 and "access_token" not in rejected.json()
        checked(client.put(grant_path, headers=admin, json=grant))
        restored = checked(
            redeem(
                client,
                base,
                config,
                browser(
                    client,
                    base,
                    config,
                    scopes="User.Read AuditLog.Read.All",
                    user={"username": user["upn"], "password": password},
                ),
            )
        )
        checked(client.get(report, headers={"Authorization": "Bearer " + restored["access_token"]}))
        return {"role_effective_access": True, "grant_effective_access": True}
    finally:
        checked(client.delete(role_path, headers=admin))
        checked(client.delete(grant_path, headers=admin))
        checked(client.delete(base + "/v1.0/users/" + user["id"], headers=headers), 204)

"""Original Graph PowerShell against explicit local HTTPS, including real local PKCE/MFA."""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import secrets
import shutil
import ssl
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx

from mockazure.admin.client import AdminClient
from mockazure.asyncio_support import run
from mockazure.config import load_settings
from mockazure.population.delegated_login import REDIRECT, Form
from mockazure.security.roles import role_ids
from tools.prepare_clients import prepare
from tools.verify_collections import PS_IMAGE


def checked(response, expected=200):
    if response.status_code != expected:
        raise RuntimeError(f"Local PIM setup returned HTTP {response.status_code}")
    return response


async def delegated_token(client, login, tenant, app, secret, user, password, scopes, method):
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    response = checked(
        await client.get(
            f"{login}/{tenant}/oauth2/v2.0/authorize",
            params={
                "client_id": app,
                "redirect_uri": REDIRECT,
                "response_type": "code",
                "scope": " ".join(scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
    )
    for action, body in (
        ("/login", {"username": user, "password": password}),
        ("/consent", {"decision": "approve"}),
        ("/mfa", {"decision": "complete", "method_id": method}),
    ):
        form = Form(response.text)
        if form.action != action or set(form.fields) != {"transaction", "csrf"}:
            raise RuntimeError("Local PKCE did not reach the expected interactive step")
        response = await client.post(login + action, data=form.fields | body)
        checked(response, 303 if action == "/mfa" else 200)
    location = response.headers.get("location", "")
    query = parse_qs(urlsplit(location).query)
    if location.split("?")[0] != REDIRECT or query.get("state") != [state]:
        raise RuntimeError("Local OAuth callback did not match")
    response = checked(
        await client.post(
            f"{login}/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "authorization_code",
                "client_id": app,
                "client_secret": secret,
                "code_verifier": verifier,
                "code": query["code"][0],
                "redirect_uri": REDIRECT,
            },
        )
    )
    return response.json()["access_token"]


async def qualify(config, report, *, prepare_clients=False):
    settings = load_settings(config)
    for endpoint in (settings.endpoints.graph, settings.endpoints.login):
        parts = urlsplit(endpoint)
        if parts.scheme != "https" or parts.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("Qualification requires explicit loopback HTTPS endpoints")
    vendor = Path("work/pim-clients").resolve()
    manifest = json.loads(Path("integrations/manifest.json").read_text())
    manifest["artifacts"] = [
        a
        for a in manifest["artifacts"]
        if a["name"] in {"Microsoft.Graph.Authentication", "Microsoft.Graph.Identity.Governance"}
    ]
    pin = Path("work/pim-clients-manifest.json")
    pin.write_text(json.dumps(manifest))
    prepare(pin, vendor, verify_only=not prepare_clients)
    stamp = uuid4().hex[:10]
    password = secrets.token_urlsafe(32)
    async with AdminClient(
        str(settings.admin_base_url), ca_file=settings.ca_file, token_file=settings.token_file
    ) as admin:
        tenant = await admin.request(
            "POST",
            "/_mock/tenants",
            json={
                "slug": "pim-" + stamp,
                "display_name": "Native PIM qualification",
                "domain": "pim-" + stamp + ".test",
                "licences": ["E5"],
            },
        )
        tid = tenant["id"]
        base = f"/_mock/tenants/{tid}/identity"
        admin_scope = ["RoleManagement.ReadWrite.Directory"]
        scopes = ["RoleAssignmentSchedule.ReadWrite.Directory", "AuditLog.Read.All"]
        user = await admin.request(
            "POST",
            base + "/users",
            json={
                "upn": "analyst@" + tenant["domain"],
                "display_name": "Local PIM analyst",
                "password": password,
            },
        )
        # Public licensing inventory determines the P2 entitlement gate.
        await admin.request(
            "PUT",
            f"/_mock/tenants/{tid}/licensing/skus/84a661c4-e949-4bd2-a560-ed7766fcaf2b",
            json={
                "sku_part_number": "AAD_PREMIUM_P2",
                "status": "Enabled",
                "enabled": 10,
                "suspended": 0,
                "warning": 0,
                "allowed_usage_locations": ["US"],
                "service_plans": [
                    {
                        "servicePlanId": "eec0eb4f-6444-4f95-aba0-50c24d67f998",
                        "servicePlanName": "AAD_PREMIUM_P2",
                        "provisioningStatus": "Success",
                        "appliesTo": "User",
                    }
                ],
            },
        )
        method = await admin.request(
            "POST",
            f"/_mock/tenants/{tid}/security/users/" + user["id"] + "/authenticationMethods",
            json={
                "@odata.type": "#microsoft.graph.phoneAuthenticationMethod",
                "phoneType": "mobile",
                "phoneNumber": "+1 5555550123",
            },
        )
        clients = []
        for kind, permissions in (("application", admin_scope), ("delegated", scopes)):
            app = await admin.request(
                "POST",
                base + "/applications",
                json={
                    "display_name": "PIM " + kind,
                    "client_mode": "confidential",
                    "redirect_uris": [REDIRECT] if kind == "delegated" else [],
                    "required_permissions": [
                        {"resource": "graph", "kind": kind, "permissions": permissions}
                    ],
                },
            )
            credential = await admin.request(
                "POST",
                base + "/applications/" + app["client_id"] + "/credentials",
                json={"kind": "secret"},
            )
            await admin.request(
                "PUT",
                base + "/grants/" + str(uuid4()),
                json={
                    "client_id": app["client_id"],
                    "resource": "graph",
                    "kind": kind,
                    "permissions": permissions,
                    "user_id": user["id"] if kind == "delegated" else None,
                },
            )
            clients.append((app["client_id"], credential["secret"]))
    tls = ssl.create_default_context(cafile=str(settings.ca_file))
    async with httpx.AsyncClient(verify=tls, trust_env=False, follow_redirects=False) as client:
        app, secret = clients[0]
        response = checked(
            await client.post(
                f"{settings.endpoints.login}/{tid}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": app,
                    "client_secret": secret,
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
        )
        admin_token = response.json()["access_token"]
        user_token = await delegated_token(
            client,
            settings.endpoints.login,
            tid,
            *clients[1],
            user["upn"],
            password,
            scopes,
            method["id"],
        )
    project = Path.cwd().resolve()
    profile = {
        "endpoint": settings.endpoints.graph,
        "admin_token": admin_token,
        "delegated_token": user_token,
        "user_id": user["id"],
        "role_id": role_ids()["Security Reader"],
        "ca_file": str(settings.ca_file),
        "vendor": str(vendor),
        "project": str(project),
        "relay": False,
    }
    command = [
        shutil.which("pwsh") or "pwsh",
        "-NoProfile",
        "-File",
        str(project / "integrations/powershell/Test-LivingPim.ps1"),
    ]
    if os.name == "nt":
        profile.update(ca_file="/lab/ca.pem", vendor="/vendor", project="/project", relay=True)
        command = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--env",
            "SSL_CERT_FILE=/lab/ca.pem",
            "--mount",
            f"type=bind,source={vendor},target=/vendor,readonly",
            "--mount",
            f"type=bind,source={settings.ca_file},target=/lab/ca.pem,readonly",
            "--mount",
            f"type=bind,source={project / 'integrations'},target=/project/integrations,readonly",
            PS_IMAGE,
            "pwsh",
            "-NoProfile",
            "-File",
            "/project/integrations/powershell/Test-LivingPim.ps1",
        ]
    outcome = await asyncio.to_thread(
        subprocess.run,
        command,
        input=json.dumps(profile),
        text=True,
        capture_output=True,
        timeout=180,
        env=dict(os.environ, SSL_CERT_FILE=str(settings.ca_file)),
    )
    if outcome.returncode:
        error = outcome.stderr
        for value in (admin_token, user_token, password, *[secret for _, secret in clients]):
            error = error.replace(value, "[redacted]")
        (settings.data_dir / "pim-qualification-error.log").write_text(error, encoding="utf-8")
        raise RuntimeError(
            "Original Graph PIM qualification failed; inspect the redacted local log"
        )
    result = json.loads(outcome.stdout[outcome.stdout.index('{"state"') :].strip())
    result.update(
        tenant_id=tid,
        user_id=user["id"],
        local_pkce_mfa=True,
        api_runtime="native",
        client_runtime="isolated-linux" if os.name == "nt" else "native",
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--prepare-clients",
        action="store_true",
        help="Download and verify the two pinned public Microsoft client archives",
    )
    args = parser.parse_args()
    run(qualify(args.config, args.report, prepare_clients=args.prepare_clients))

"""Original method-management clients plus local reset/password-change qualification."""

import base64
import hashlib
import html
import json
import re
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from tools.prepare_clients import prepare
from tools.verify_collections import PS_IMAGE
from tools.verify_default import ROOT
from tools.verify_default import main as smoke


def qualify(directory, command, compose, client, headers, tenant, check):
    state = directory / "method-management-clients"
    state.mkdir()
    base = "/_mock/tenants/" + tenant["id"]

    def call(method, path, body=None, status=200, auth=headers):
        result = client.request(method, path, headers=auth, json=body)
        assert result.status_code == status, (path, result.status_code, result.text[:200])
        return result.json() if result.content else None

    permissions = ["UserAuthenticationMethod.ReadWrite.All", "UserAuthMethod-Passkey.Read.All"]
    app = call(
        "POST",
        base + "/identity/applications",
        {
            "display_name": "Method clients",
            "client_mode": "confidential",
            "required_permissions": [
                {"resource": "graph", "kind": "application", "permissions": permissions}
            ],
        },
        201,
    )
    secret = call(
        "POST",
        base + f"/identity/applications/{app['client_id']}/credentials",
        {"kind": "secret"},
        201,
    )
    call(
        "PUT",
        base + f"/identity/grants/{uuid4()}",
        {
            "client_id": app["client_id"],
            "resource": "graph",
            "kind": "application",
            "permissions": permissions,
        },
    )
    public = call(
        "POST",
        base + "/identity/applications",
        {
            "display_name": "Reset administrator",
            "client_mode": "public",
            "redirect_uris": ["http://localhost:8123/callback"],
            "required_permissions": [
                {
                    "resource": "graph",
                    "kind": "delegated",
                    "permissions": ["UserAuthenticationMethod.ReadWrite.All", "User.Read"],
                }
            ],
        },
        201,
    )
    call(
        "PUT",
        base + f"/identity/grants/{uuid4()}",
        {
            "client_id": public["client_id"],
            "resource": "graph",
            "kind": "delegated",
            "permissions": ["UserAuthenticationMethod.ReadWrite.All", "User.Read"],
        },
    )
    admin = call(
        "POST",
        base + "/identity/users",
        {
            "upn": "reset-admin@" + tenant["domain"],
            "display_name": "Reset operator",
            "password": "Native local admin password 42!",
        },
        201,
    )
    call(
        "PUT", base + f"/identity/users/{admin['id']}/roles/Privileged Authentication Administrator"
    )
    (state / "admin-user.json").write_text(
        json.dumps({"username": admin["upn"], "password": "Native local admin password 42!"}),
        encoding="utf-8",
    )
    targets = {}
    for label in ("python", "powershell"):
        user = call(
            "POST",
            base + "/identity/users",
            {
                "upn": label + "@" + tenant["domain"],
                "display_name": label,
                "password": "Initial native password 42!",
            },
            201,
        )
        targets[label + "_user_id"] = user["id"]
        for field, kind in (
            ("fido", "fido2AuthenticationMethod"),
            ("authenticator", "microsoftAuthenticatorAuthenticationMethod"),
        ):
            method = call(
                "POST",
                base + f"/security/users/{user['id']}/authenticationMethods",
                {"@odata.type": "#microsoft.graph." + kind, "displayName": "Local descriptor"},
                201,
            )
            targets[label + "_" + field + "_id"] = method["id"]
    origin = "https://localhost:8443"
    config = {
        "tenant_id": tenant["id"],
        "client_id": app["client_id"],
        "public_client_id": public["client_id"],
        **targets,
        "secret_file": "/state/secret.json",
        "ca_file": "/certs/ca.pem",
        "endpoints": {"login": origin, "graph": origin},
    }
    (state / "client.json").write_text(json.dumps(config), encoding="utf-8")
    (state / "secret.json").write_text(json.dumps({"secret": secret["secret"]}), encoding="utf-8")
    check("local_method_descriptors_and_reset_authority_provisioned")
    api = command(compose + ["ps", "-q", "api"], capture=True)
    common = [
        "docker",
        "run",
        "--rm",
        "--network",
        "container:" + api,
        "-e",
        "SSL_CERT_FILE=/certs/ca.pem",
        "-e",
        "HTTP_PROXY=",
        "-e",
        "HTTPS_PROXY=",
        "-e",
        "ALL_PROXY=",
        "-e",
        "NO_PROXY=*",
        "--mount",
        f"type=bind,source={directory / '.runtime/instance/ca.pem'},target=/certs/ca.pem,readonly",
        "--mount",
        f"type=bind,source={ROOT / 'integrations'},target=/project/integrations,readonly",
        "--mount",
        f"type=bind,source={state},target=/state",
    ]

    image = "mockazure:method-management-python-" + directory.name.removeprefix("default-smoke-")
    command(["docker", "build", "-f", "integrations/Dockerfile.python", "-t", image, "."], cwd=ROOT)
    vendor = ROOT / "work/method-management-clients-vendor"
    prepare(ROOT / "integrations/method-management-manifest.json", vendor, verify_only=True)

    command(common + [image, "/project/integrations/python/method_management.py"])
    check("original_sdk_phone_email_hardware_delete_and_delegated_reset")
    for phase in ("methods", "reset"):
        command(
            common
            + [
                "-e",
                "POWERSHELL_TELEMETRY_OPTOUT=1",
                "-e",
                "DOTNET_CLI_TELEMETRY_OPTOUT=1",
                "-e",
                "PSModulePath=/vendor/modules:/opt/microsoft/powershell/7/Modules",
                "--mount",
                f"type=bind,source={vendor / 'modules'},target=/vendor/modules,readonly",
                PS_IMAGE,
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-File",
                "/project/integrations/powershell/Test-MethodManagement.ps1",
                "-Phase",
                phase,
            ]
        )
    check("original_powershell_method_lifecycle_and_delegated_reset")
    # Exercise the served browser forms and cookie/CSRF state against the actual HTTPS issuer.
    verifier = "v" * 64
    endpoint = "/" + tenant["id"] + "/oauth2/v2.0"
    params = {
        "client_id": public["client_id"],
        "redirect_uri": "http://localhost:8123/callback",
        "response_type": "code",
        "scope": "User.Read",
        "code_challenge_method": "S256",
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("="),
    }
    page = client.get(endpoint + "/authorize", params=params)
    assert page.status_code == 200
    fields = {
        name: html.unescape(re.search(r'name="' + name + r'" value="([^"]*)"', page.text)[1])
        for name in ("transaction", "csrf")
    }
    signed = client.post(
        "/login",
        data={
            **fields,
            "username": "python@" + tenant["domain"],
            "password": "Native temporary password 72!",
        },
    )
    assert signed.status_code == 200 and 'action="/password-change"' in signed.text
    changed = client.post(
        "/password-change",
        data={
            **fields,
            "new_password": "Final native password 93!",
            "confirm_password": "Final native password 93!",
        },
    )
    assert changed.status_code == 200 and 'action="/consent"' in changed.text
    assert client.post("/consent", data={**fields, "decision": "approve"}).status_code == 200
    callback = client.post("/mfa", data={**fields, "decision": "skip"})
    assert callback.status_code == 303
    code = parse_qs(urlsplit(callback.headers["location"]).query)["code"][0]
    tokens = client.post(
        endpoint + "/token",
        data={
            "client_id": public["client_id"],
            "grant_type": "authorization_code",
            "redirect_uri": "http://localhost:8123/callback",
            "code": code,
            "code_verifier": verifier,
        },
    )
    assert tokens.status_code == 200
    me = client.get(
        "/v1.0/me", headers={"Authorization": "Bearer " + tokens.json()["access_token"]}
    )
    assert me.status_code == 200 and me.json()["id"] == targets["python_user_id"]
    check("actual_https_password_change_csrf_cookie_and_new_token_flow")
    prepare(ROOT / "integrations/method-management-manifest.json", vendor, verify_only=True)
    check("original_module_bytes_unchanged")
    reports = {
        name: json.loads((state / name).read_text(encoding="utf-8-sig"))
        for name in ("python-report.json", "powershell-report.json")
    }
    (directory / "method-management-report.json").write_text(
        json.dumps(reports, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    smoke(qualify=qualify)

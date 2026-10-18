"""Original policy clients and fresh-profile HTTPS authentication qualification."""

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
    state = directory / "authentication-policy-clients"
    state.mkdir()
    base = "/_mock/tenants/" + tenant["id"]

    def call(method, path, body=None, status=200, auth=headers):
        result = client.request(method, path, headers=auth, json=body)
        assert result.status_code == status, (path, result.status_code, result.text[:200])
        return result.json() if result.content else None

    call(
        "PUT",
        base + "/licensing/skus/84a661c4-e949-4bd2-a560-ed7766fcaf2b",
        {
            "sku_part_number": "AAD_PREMIUM_P2",
            "status": "Enabled",
            "enabled": 100,
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
    permissions = [
        "Policy.ReadWrite.AuthenticationMethod",
        "Policy.Read.All",
        "Policy.ReadWrite.ConditionalAccess",
    ]
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
                    "permissions": ["Policy.ReadWrite.AuthenticationMethod", "User.Read"],
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
            "permissions": ["Policy.ReadWrite.AuthenticationMethod", "User.Read"],
        },
    )
    admin = call(
        "POST",
        base + "/identity/users",
        {
            "upn": "policy-admin@" + tenant["domain"],
            "display_name": "Reset operator",
            "password": "Native local admin password 42!",
        },
        201,
    )
    call("PUT", base + f"/identity/users/{admin['id']}/roles/Authentication Policy Administrator")
    call(
        "POST",
        base + f"/security/users/{admin['id']}/authenticationMethods",
        {
            "@odata.type": "#microsoft.graph.phoneAuthenticationMethod",
            "phoneType": "mobile",
            "phoneNumber": "+1 5555550199",
        },
        201,
    )
    (state / "admin-user.json").write_text(
        json.dumps({"username": admin["upn"], "password": "Native local admin password 42!"}),
        encoding="utf-8",
    )
    origin = "https://localhost:8443"
    config = {
        "tenant_id": tenant["id"],
        "client_id": app["client_id"],
        "public_client_id": public["client_id"],
        "delegated_scopes": ["Policy.ReadWrite.AuthenticationMethod"],
        "secret_file": "/state/secret.json",
        "ca_file": "/certs/ca.pem",
        "endpoints": {"login": origin, "graph": origin},
    }
    (state / "client.json").write_text(json.dumps(config), encoding="utf-8")
    (state / "secret.json").write_text(json.dumps({"secret": secret["secret"]}), encoding="utf-8")
    check("registered_sms_and_policy_administrator_provisioned")
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

    image = "mockazure:authentication-policy-python-" + directory.name.removeprefix(
        "default-smoke-"
    )
    command(["docker", "build", "-f", "integrations/Dockerfile.python", "-t", image, "."], cwd=ROOT)
    vendor = ROOT / "work/method-management-clients-vendor"
    prepare(ROOT / "integrations/method-management-manifest.json", vendor, verify_only=True)

    command(common + [image, "/project/integrations/python/authentication_policies.py"])
    check("original_sdk_policy_configuration_and_extended_ca")
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
            "/project/integrations/powershell/Test-AuthenticationPolicies.ps1",
        ]
    )
    check("original_powershell_delegated_method_policy_configuration")
    # Actual HTTPS browser forms must select a currently registered method.
    verifier = "v" * 64
    endpoint = "/" + tenant["id"] + "/oauth2/v2.0"
    params = {
        "client_id": public["client_id"],
        "redirect_uri": "http://localhost:8123/callback",
        "response_type": "code",
        "scope": "User.Read offline_access",
        "code_challenge_method": "S256",
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("="),
    }
    page = client.get(endpoint + "/authorize", params=params)
    fields = {
        name: html.unescape(re.search(r'name="' + name + r'" value="([^"]*)"', page.text)[1])
        for name in ("transaction", "csrf")
    }
    assert (
        client.post(
            "/login",
            data={
                **fields,
                "username": admin["upn"],
                "password": "Native local admin password 42!",
            },
        ).status_code
        == 200
    )
    challenge = client.post("/consent", data={**fields, "decision": "approve"})
    selection = re.search(r'<option value="([^"]+)"', challenge.text)
    assert selection, "Fresh profile did not offer the enrolled SMS method"
    callback = client.post(
        "/mfa", data={**fields, "decision": "complete", "method_id": html.unescape(selection[1])}
    )
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
    assert tokens.status_code == 200, tokens.text
    token = client.post(
        endpoint + "/token",
        data={
            "client_id": app["client_id"],
            "client_secret": secret["secret"],
            "grant_type": "client_credentials",
            "scope": "https://graph.microsoft.com/.default",
        },
    ).json()["access_token"]
    auth = {"Authorization": "Bearer " + token}
    sms_path = "/v1.0/policies/authenticationMethodsPolicy/authenticationMethodConfigurations/Sms"
    call(
        "PATCH",
        sms_path,
        {
            "@odata.type": "#microsoft.graph.smsAuthenticationMethodConfiguration",
            "state": "disabled",
        },
        204,
        auth,
    )
    refresh = client.post(
        endpoint + "/token",
        data={
            "client_id": public["client_id"],
            "grant_type": "refresh_token",
            "refresh_token": tokens.json()["refresh_token"],
        },
    )
    assert refresh.status_code == 400 and refresh.json()["error"] == "interaction_required"
    call(
        "PATCH",
        sms_path,
        {
            "@odata.type": "#microsoft.graph.smsAuthenticationMethodConfiguration",
            "state": "enabled",
        },
        204,
        auth,
    )
    profile = call("GET", base + "/identity/authentication-policy-profile")
    assert profile["method_policy_enforced"] is True
    check("actual_https_registered_mfa_and_policy_disable_blocks_refresh")
    prepare(ROOT / "integrations/method-management-manifest.json", vendor, verify_only=True)
    check("original_module_bytes_unchanged")
    reports = {
        name: json.loads((state / name).read_text(encoding="utf-8-sig"))
        for name in ("python-report.json", "powershell-report.json")
    }
    (directory / "authentication-policy-report.json").write_text(
        json.dumps(reports, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    smoke(qualify=qualify)

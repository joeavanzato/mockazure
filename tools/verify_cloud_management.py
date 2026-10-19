"""Four original management clients against one fresh local Compose runtime."""

import json
import sys
import time
from uuid import uuid4

import msal

from integrations.python.arm import LocalSession
from tools.cloud_management_fixtures import resources
from tools.verify_collections import PS_IMAGE
from tools.verify_default import ROOT
from tools.verify_default import main as smoke


def qualify(directory, command, compose, client, headers, tenant, check):
    state = directory / "cloud-clients"
    state.mkdir()
    base = "/_mock/tenants/" + tenant["id"]

    def request(method, path, value=None, status=200):
        response = client.request(method, path, headers=headers, json=value)
        assert response.status_code == status, (path, response.status_code)
        return response.json()

    app = request(
        "POST",
        base + "/identity/applications",
        {
            "display_name": "Cloud management clients",
            "client_mode": "confidential",
            "required_permissions": [
                {
                    "resource": "graph",
                    "kind": "application",
                    "permissions": ["Application.Read.All"],
                }
            ],
        },
        201,
    )
    identifier = app["client_id"]
    secret = request(
        "POST", base + f"/identity/applications/{identifier}/credentials", {"kind": "secret"}, 201
    )
    request(
        "PUT",
        base + "/identity/grants/" + str(uuid4()),
        {
            "client_id": identifier,
            "resource": "graph",
            "kind": "application",
            "permissions": ["Application.Read.All"],
        },
    )
    origin = str(client.base_url).rstrip("/")
    ca = str(directory / ".runtime/instance/ca.pem")
    with LocalSession(origin, ca) as transport:
        auth = msal.ConfidentialClientApplication(
            identifier,
            client_credential=secret["secret"],
            authority=origin + "/" + tenant["id"],
            validate_authority=False,
            instance_discovery=False,
            http_client=transport,
        )
        arm_token = auth.acquire_token_for_client(["https://management.azure.com/.default"])
        graph_token = auth.acquire_token_for_client(["https://graph.microsoft.com/.default"])
    assert "access_token" in arm_token and "access_token" in graph_token
    principal = client.get(
        "/v1.0/servicePrincipals",
        params={"$filter": f"appId eq '{identifier}'"},
        headers={"Authorization": "Bearer " + graph_token["access_token"]},
    ).json()["value"][0]["id"]
    sub = request(
        "POST", base + "/subscriptions", {"display_name": "Cloud workflow qualification"}, 201
    )["id"]
    subbase = base + "/subscriptions/" + sub
    request(
        "PUT",
        subbase + "/role-assignments/" + str(uuid4()),
        {
            "principal_id": principal,
            "principal_type": "ServicePrincipal",
            "scope": "/subscriptions/" + sub,
            "role": "Owner",
        },
    )
    bearer = {"Authorization": "Bearer " + arm_token["access_token"]}
    for namespace in (
        "Microsoft.Network",
        "Microsoft.Compute",
        "Microsoft.KeyVault",
        "Microsoft.OperationalInsights",
        "Microsoft.Storage",
    ):
        response = client.post(
            "/subscriptions/" + sub + "/providers/" + namespace + "/register",
            params={"api-version": "2021-04-01"},
            headers=bearer,
        )
        assert response.status_code == 200
    # The PowerShell container shares only the isolated API network namespace and CA.
    instance = directory / ".runtime/instance/container.json"
    runtime = json.loads(instance.read_text())
    runtime["client_profiles"]["container"] = {
        name: "https://localhost:8443" for name in ("login", "graph", "exchange", "arm", "activity")
    }
    instance.write_text(json.dumps(runtime, indent=2) + "\n")
    command(compose + ["restart", "api"])
    for _ in range(120):
        try:
            if client.get("/_mock/ready").status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        raise RuntimeError("Cloud API restart failed")
    api = command(compose + ["ps", "-q", "api"], capture=True)
    token_file = state / "token.json"
    token_file.write_text(json.dumps(arm_token))
    profiles = {}
    results = {}
    prior = []
    for ecosystem in ("python", "typescript", "dotnet", "powershell"):
        name = "workflow-" + ecosystem
        request("PUT", subbase + "/resource-groups/" + name, {"location": "eastus"})
        group = "/subscriptions/" + sub + "/resourceGroups/" + name
        rows = resources(group, tenant["id"], ecosystem)
        profile = {
            "endpoint": origin,
            "tenant_id": tenant["id"],
            "client_id": identifier,
            "subscription_id": sub,
            "token_file": str(token_file),
            "ca_file": ca,
            "resources": rows,
            "read_resources": prior,
        }
        profiles[ecosystem] = profile
        prior = rows

    # Python closes the interoperability ring by reading PowerShell after all writes.
    def run(ecosystem, workflow):
        profile = profiles[ecosystem].copy()
        profile_file = state / (ecosystem + ".json")
        report = state / (ecosystem + "-" + workflow + ".json")
        if ecosystem == "powershell":
            profile.update(
                endpoint="https://localhost:8443",
                ca_file="/certs/ca.pem",
                token_file="/state/token.json",
            )
        profile_file.write_text(json.dumps(profile, indent=2))
        if ecosystem == "python":
            args = [
                sys.executable,
                "-m",
                "integrations.python.cloud_management",
                "--profile",
                str(profile_file),
                "--workflow",
                workflow,
                "--report",
                str(report),
            ]
        elif ecosystem == "typescript":
            env_script = ROOT / "work/cloud-b-clients/npm/dist/runner.js"
            # Configure the instance CA only in this child process.
            args = [
                sys.executable,
                "-m",
                "tools.run_cloud_client",
                "--ca",
                ca,
                "--",
                "node",
                str(env_script),
                "--profile",
                str(profile_file),
                "--workflow",
                workflow,
                "--report",
                str(report),
            ]
        elif ecosystem == "dotnet":
            args = [
                "dotnet",
                str(ROOT / "work/cloud-b-clients/dotnet/CloudWorkflows.dll"),
                "--profile",
                str(profile_file),
                "--workflow",
                workflow,
                "--report",
                str(report),
            ]
        else:
            args = [
                "docker",
                "run",
                "--rm",
                "--network",
                "container:" + api,
                "-e",
                "SSL_CERT_FILE=/certs/ca.pem",
                "-e",
                "PSModulePath=/vendor/modules:/opt/microsoft/powershell/7/Modules",
                "-e",
                "POWERSHELL_TELEMETRY_OPTOUT=1",
                "-e",
                "DOTNET_CLI_TELEMETRY_OPTOUT=1",
                "-e",
                "HTTP_PROXY=",
                "-e",
                "HTTPS_PROXY=",
                "-e",
                "ALL_PROXY=",
                "-e",
                "NO_PROXY=*",
                "--mount",
                f"type=bind,source={ca},target=/certs/ca.pem,readonly",
                "--mount",
                f"type=bind,source={ROOT / 'integrations'},target=/project/integrations,readonly",
                "--mount",
                f"type=bind,source={ROOT / 'work/vendor'},target=/vendor,readonly",
                "--mount",
                f"type=bind,source={state},target=/state",
                PS_IMAGE,
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-File",
                "/project/integrations/powershell/Test-CloudManagement.ps1",
                "-Profile",
                "/state/" + profile_file.name,
                "-Workflow",
                workflow,
                "-Report",
                "/state/" + report.name,
            ]
        command(args, cwd=ROOT, timeout=600)
        result = json.loads(report.read_text(encoding="utf-8-sig"))
        assert result["passed"]
        check("cloud_" + ecosystem + "_" + workflow)
        return result

    for ecosystem in profiles:
        results[ecosystem] = run(ecosystem, "roundtrip")
    profiles["python"]["read_resources"] = profiles["powershell"]["resources"]
    results["python"]["cross_read"] = run("python", "cross-read")
    for row in profiles["powershell"]["resources"]:
        response = client.get(row["id"], params={"api-version": row["version"]}, headers=bearer)
        assert response.status_code == 200 and response.json()["id"].lower() == row["id"].lower()
    for ecosystem in profiles:
        results[ecosystem]["cleanup"] = run(ecosystem, "cleanup")
        for row in profiles[ecosystem]["resources"]:
            removed = client.get(row["id"], params={"api-version": row["version"]}, headers=bearer)
            assert removed.status_code == 404, (ecosystem, row["type"], removed.status_code)

    result = {"passed": True, "clients": results, "resourceFamilies": 12, "localOnly": True}
    (directory / "cloud-management-report.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    smoke(qualify=qualify)

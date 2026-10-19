"""Four original management clients against one fresh local Compose runtime."""

import json
import sys
import time
from uuid import uuid4

import msal

from integrations.python.arm import LocalSession
from tools.verify_collections import PS_IMAGE
from tools.verify_default import ROOT
from tools.verify_default import main as smoke


def qualify(directory, command, compose, client, headers, tenant, check):
    state = directory / "query-clients"
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
        logs_token = auth.acquire_token_for_client(["https://api.loganalytics.io/.default"])
        graph_token = auth.acquire_token_for_client(["https://graph.microsoft.com/.default"])
    assert all("access_token" in token for token in (arm_token, graph_token, logs_token))
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
    for namespace in ("Microsoft.Storage", "Microsoft.OperationalInsights"):
        response = client.post(
            f"/subscriptions/{sub}/providers/{namespace}/register",
            params={"api-version": "2021-04-01"},
            headers=bearer,
        )
        assert response.status_code == 200
    instance = directory / ".runtime/instance/container.json"
    runtime = json.loads(instance.read_text())
    runtime["client_profiles"]["container"] = {
        **{
            name: "https://localhost:8443"
            for name in ("login", "graph", "exchange", "arm", "activity")
        },
        **{
            name: f"https://localhost:{port}"
            for name, port in [
                ("storage", 8444),
                ("file", 8445),
                ("queue", 8446),
                ("table", 8447),
                ("keyvault", 8448),
                ("logs", 8449),
            ]
        },
    }
    instance.write_text(json.dumps(runtime, indent=2) + "\n")
    command(compose + ["restart", "api", "worker"])
    from tools.verify_graph_workflows import wait_for_fresh_connection

    wait_for_fresh_connection(origin, ca)
    api = command(compose + ["ps", "-q", "api"], capture=True)
    token_file = state / "token.json"
    token_file.write_text(json.dumps(arm_token))
    logs_file = state / "logs-token.json"
    logs_file.write_text(json.dumps(logs_token))
    request("PUT", subbase + "/resource-groups/queries", {"location": "eastus"})
    group = f"/subscriptions/{sub}/resourceGroups/queries"

    def create(path, body, version):
        response = client.put(path, headers=bearer, params={"api-version": version}, json=body)
        assert response.status_code in (200, 201), (path, response.text)
        return response.json()

    wid_path = group + "/providers/Microsoft.OperationalInsights/workspaces/evidence"
    workspace = create(
        wid_path,
        {"location": "eastus", "properties": {"sku": {"name": "PerGB2018"}, "retentionInDays": 30}},
        "2025-02-01",
    )
    ecosystems = ("python", "typescript", "dotnet", "powershell")
    accounts = {}
    for ecosystem in ecosystems:
        name = "nativequery" + ecosystem
        path = group + "/providers/Microsoft.Storage/storageAccounts/" + name
        resource = create(
            path,
            {
                "location": "eastus",
                "kind": "StorageV2",
                "sku": {"name": "Standard_LRS"},
                "properties": {},
            },
            "2025-06-01",
        )
        accounts[ecosystem] = (path, resource["properties"]["primaryEndpoints"]["blob"])
    from urllib.parse import urlsplit

    logs_endpoint = f"https://localhost:{urlsplit(origin).port + 6}"
    profiles = {
        ecosystem: {
            "endpoint": origin,
            "logs_endpoint": logs_endpoint,
            "tenant_id": tenant["id"],
            "client_id": identifier,
            "subscription_id": sub,
            "token_file": str(token_file),
            "logs_token_file": str(logs_file),
            "ca_file": ca,
            "source": accounts[ecosystem][0] + "/blobServices/default",
            "account": accounts[ecosystem][0],
            "setting": "collector-" + ecosystem,
            "workspace_resource": wid_path,
            "workspace_id": workspace["properties"]["customerId"],
            "inventory_query": (
                "Resources | where type == 'microsoft.storage/storageaccounts' "
                "| project id | order by id asc"
            ),
            "expected_resources": [v[0].lower() for v in accounts.values()],
            "logs_query": (
                "StorageBlobLogs | where OperationName == 'PutBlob' | summarize n=count()"
            ),
            "expected_logs": 4,
        }
        for ecosystem in ecosystems
    }

    def run(ecosystem, workflow, label):
        p = profiles[ecosystem].copy()
        profile_file = state / (ecosystem + ".json")
        report = state / (ecosystem + "-" + label + ".json")
        if ecosystem == "powershell":
            p.update(
                endpoint="https://localhost:8443",
                logs_endpoint="https://localhost:8449",
                ca_file="/certs/ca.pem",
                token_file="/state/token.json",
                logs_token_file="/state/logs-token.json",
            )
        profile_file.write_text(json.dumps(p, indent=2))
        suffix = ["--profile", str(profile_file), "--workflow", workflow, "--report", str(report)]
        if ecosystem == "python":
            args = [sys.executable, "-m", "integrations.python.cloud_queries", *suffix]
        elif ecosystem == "typescript":
            args = [
                sys.executable,
                "-m",
                "tools.run_cloud_client",
                "--ca",
                ca,
                "--",
                "node",
                str(ROOT / "work/cloud-f-clients/npm/runner.js"),
                *suffix,
            ]
        elif ecosystem == "dotnet":
            args = ["dotnet", str(ROOT / "work/cloud-f-clients/dotnet/CloudQueries.dll"), *suffix]
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
                f"type=bind,source={ROOT / 'work/cloud-f-vendor'},target=/vendor,readonly",
                "--mount",
                f"type=bind,source={state},target=/state",
                PS_IMAGE,
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-File",
                "/project/integrations/powershell/Test-CloudQueries.ps1",
                "-Profile",
                "/state/" + profile_file.name,
                "-Workflow",
                workflow,
                "-Report",
                "/state/" + report.name,
            ]
        command(args, cwd=ROOT)
        result = json.loads(report.read_text(encoding="utf-8-sig"))
        assert result["passed"]
        return result

    report = {"passed": False, "localOnly": True, "written": {}, "read": {}, "restarted": {}}
    report_file = directory / "cloud-queries-report.json"
    try:
        for ecosystem in ecosystems:
            report["written"][ecosystem] = run(ecosystem, "write", "write")
        # Original Blob SDK writes real source content, not imported evidence.
        from azure.core.pipeline.transport import RequestsTransport
        from azure.storage.blob import BlobServiceClient

        for ecosystem, (account, endpoint) in accounts.items():
            response = client.post(
                account + "/listKeys", headers=bearer, params={"api-version": "2025-06-01"}
            )
            assert response.status_code == 200
            key = response.json()["keys"][0]["value"]
            with LocalSession("https://" + urlsplit(endpoint).netloc, ca) as transport:
                blobs = BlobServiceClient(
                    endpoint,
                    credential=key,
                    api_version="2025-11-05",
                    transport=RequestsTransport(session=transport, connection_verify=ca),
                )
                blobs.create_container("evidence")
                blobs.get_blob_client("evidence", ecosystem + ".txt").upload_blob(
                    b"local diagnostic evidence", overwrite=True
                )
        import httpx

        with httpx.Client(base_url=logs_endpoint, verify=ca, trust_env=False) as logs:
            for _ in range(120):
                response = logs.post(
                    "/v1/workspaces/" + workspace["properties"]["customerId"] + "/query",
                    headers={"Authorization": "Bearer " + logs_token["access_token"]},
                    json={"query": profiles["python"]["logs_query"]},
                )
                if response.status_code == 200 and response.json()["tables"][0]["rows"] == [[4]]:
                    break
                time.sleep(0.5)
            else:
                raise AssertionError("Native diagnostic delivery did not become queryable")
            batch = logs.post(
                "/v1/$batch",
                headers={"Authorization": "Bearer " + logs_token["access_token"]},
                json={
                    "requests": [
                        {
                            "id": "valid",
                            "method": "POST",
                            "path": "/query",
                            "workspace": workspace["properties"]["customerId"],
                            "body": {"query": profiles["python"]["logs_query"]},
                        },
                        {
                            "id": "invalid",
                            "method": "POST",
                            "path": "/query",
                            "workspace": workspace["properties"]["customerId"],
                            "body": {"query": "NotARealTable"},
                        },
                    ]
                },
            )
            assert batch.status_code == 200 and [
                r["status"] for r in batch.json()["responses"]
            ] == [200, 400]
            report["legacy_batch_rest"] = True
        for ecosystem in ecosystems:
            report["read"][ecosystem] = run(ecosystem, "read", "read")
        command(compose + ["restart", "api", "worker"])
        wait_for_fresh_connection(origin, ca)
        for ecosystem in ecosystems:
            report["restarted"][ecosystem] = run(ecosystem, "read", "restarted")
        report["passed"] = True
        check("four_query_clients")
    finally:
        report_file.write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    smoke(qualify=qualify)

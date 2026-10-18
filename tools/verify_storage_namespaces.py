"""Original Storage namespace clients against one fresh local Compose runtime."""

import json
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4

import msal

from integrations.python.arm import LocalSession
from tools.verify_collections import PS_IMAGE
from tools.verify_default import ROOT
from tools.verify_default import main as smoke


def qualify(directory, command, compose, client, headers, tenant, check):
    state = directory / ("storage-clients-" + uuid4().hex[:8])
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
            "display_name": "Storage namespace clients",
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
    for namespace in ("Microsoft.Storage",):
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
        name: "https://127.0.0.1:8443" for name in ("login", "graph", "exchange", "arm", "activity")
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
    request("PUT", subbase + "/resource-groups/storage", {"location": "eastus"})
    account = "native" + uuid4().hex[:12]
    account_id = (
        "/subscriptions/"
        + sub
        + "/resourceGroups/storage/providers/Microsoft.Storage/storageAccounts/"
        + account
    )
    result = client.put(
        account_id,
        params={"api-version": "2025-06-01"},
        headers=bearer,
        json={"location": "eastus", "kind": "StorageV2", "sku": {"name": "Standard_LRS"}},
    )
    assert result.status_code == 200, (result.status_code, result.text)
    endpoints = result.json()["properties"]["primaryEndpoints"]
    key_result = client.post(
        account_id + "/listKeys", params={"api-version": "2025-06-01"}, headers=bearer
    )
    assert key_result.status_code == 200
    key = key_result.json()["keys"][0]["value"]
    results = {}
    for ecosystem in ("python", "typescript", "dotnet", "powershell"):
        profile = {
            "account": account,
            "key": key,
            "ca_file": ca,
            **{k + "_endpoint": v for k, v in endpoints.items()},
        }
        if ecosystem == "powershell":
            profile.update(
                ca_file="/certs/ca.pem",
                **{
                    service + "_endpoint": f"https://127.0.0.1:{8444 + offset}/{account}/"
                    for offset, service in enumerate(("blob", "file", "queue", "table"))
                },
            )
        profile_file = state / (ecosystem + ".json")
        report = state / (ecosystem + "-report.json")
        profile_file.write_text(json.dumps(profile))
        if ecosystem == "python":
            args = [
                sys.executable,
                "-m",
                "integrations.python.storage_namespaces",
                "--profile",
                str(profile_file),
                "--report",
                str(report),
            ]
        elif ecosystem == "typescript":
            args = [
                sys.executable,
                "-m",
                "tools.run_cloud_client",
                "--ca",
                ca,
                "--",
                "node",
                str(ROOT / "work/cloud-c-clients/npm/runner.js"),
                "--profile",
                str(profile_file),
                "--report",
                str(report),
            ]
        elif ecosystem == "dotnet":
            args = [
                "dotnet",
                str(ROOT / "work/cloud-c-clients/dotnet/StorageNamespaces.dll"),
                "--profile",
                str(profile_file),
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
                "/project/integrations/powershell/Test-StorageNamespaces.ps1",
                "-Profile",
                "/state/" + profile_file.name,
                "-Report",
                "/state/" + report.name,
            ]
        command(args, cwd=ROOT, timeout=300)
        result = json.loads(report.read_text(encoding="utf-8-sig"))
        assert result["passed"] and result["created"] == 2
        results[ecosystem] = result
        check("storage_namespaces_" + ecosystem)
    # Independently read every persisted client-created namespace with the
    # original Python clients, closing the cross-language interoperability loop.
    from azure.core.pipeline.transport import RequestsTransport
    from azure.storage.blob import BlobServiceClient
    from azure.storage.fileshare import ShareServiceClient

    for service, cls in [("blob", BlobServiceClient), ("file", ShareServiceClient)]:
        with LocalSession(
            urlsplit(endpoints[service]).scheme + "://" + urlsplit(endpoints[service]).netloc, ca
        ) as transport:
            sdk = cls(
                endpoints[service],
                credential={"account_name": account, "account_key": key},
                api_version="2025-11-05",
                transport=RequestsTransport(session=transport, connection_verify=ca),
                retry_total=0,
            )
            rows = sdk.list_containers() if service == "blob" else sdk.list_shares()
            assert {r.name for r in rows} == {name + "-evidence" for name in results}
    check("storage_namespace_cross_language_read")
    from tools.verify_cloud_workflows import source_identity

    commit, fingerprint = source_identity()
    result = {
        "phase": "C1",
        "passed": True,
        "localOnly": True,
        "sourceCommit": commit,
        "sourceFingerprint": fingerprint,
        "clients": results,
    }
    (directory / "storage-namespaces-report.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    from tools.prepare_storage_clients import prepare

    prepare()
    smoke(qualify=qualify)

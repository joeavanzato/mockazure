"""Original structured-service clients against a fresh, isolated Compose runtime."""

import json
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4

import msal
from cryptography.fernet import Fernet

from integrations.python.arm import LocalSession
from mockazure.config import Settings
from mockazure.tls import renew_leaf
from tools.verify_collections import PS_IMAGE
from tools.verify_default import ROOT

ECOSYSTEMS = ("python", "typescript", "dotnet", "powershell")


def ready(client, endpoints=()):
    for _ in range(120):
        try:
            if client.get("/_mock/ready").status_code == 200:
                for endpoint in dict.fromkeys(endpoints):
                    response = client.get(endpoint.rstrip("/") + "/_mock/health", timeout=3)
                    if response.status_code == 503:
                        raise RuntimeError("Data listener is not ready")
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("Structured service restart did not become ready")


def run_clients(directory, command, compose, state, check, mode):
    ca = str(directory / ".runtime/instance/ca.pem")
    api = command(compose + ["ps", "-q", "api"], capture=True)
    results = {}
    for ecosystem in ECOSYSTEMS:
        profile = state / (ecosystem + ".json")
        report = state / (ecosystem + "-" + mode + ".json")
        backup = state / (ecosystem + ".backup")
        if ecosystem == "python":
            args = [sys.executable, "-m", "integrations.python.structured_services"]
        elif ecosystem == "typescript":
            args = [
                sys.executable,
                "-m",
                "tools.run_cloud_client",
                "--ca",
                ca,
                "--",
                "node",
                str(ROOT / "work/cloud-d-clients/npm/runner.js"),
            ]
        elif ecosystem == "dotnet":
            args = ["dotnet", str(ROOT / "work/cloud-d-clients/dotnet/StructuredServices.dll")]
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
                f"type=bind,source={ROOT / 'work/cloud-d-vendor'},target=/vendor,readonly",
                "--mount",
                f"type=bind,source={state},target=/state",
                "--mount",
                f"type=bind,source={state / 'hosts'},target=/etc/hosts,readonly",
                PS_IMAGE,
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-File",
                "/project/integrations/powershell/Test-StructuredServices.ps1",
                "-Profile",
                "/state/" + profile.name,
                "-Report",
                "/state/" + report.name,
                "-Backup",
                "/state/" + backup.name,
                "-Mode",
                mode,
            ]
        if ecosystem != "powershell":
            args += [
                "--profile",
                str(profile),
                "--report",
                str(report),
                "--backup",
                str(backup),
                "--mode",
                mode,
            ]
        proxy = None
        try:
            if ecosystem == "powershell":
                proxy = "mockazure-vault-port-" + uuid4().hex[:12]
                image = command(
                    ["docker", "inspect", "--format", "{{.Config.Image}}", api], capture=True
                )
                command(
                    [
                        "docker",
                        "run",
                        "-d",
                        "--name",
                        proxy,
                        "--network",
                        "container:" + api,
                        "--entrypoint",
                        "python",
                        "--mount",
                        f"type=bind,source={ROOT / 'integrations/python/tcp_forward.py'},"
                        "target=/forward.py,readonly",
                        image,
                        "/forward.py",
                    ]
                )
                for _ in range(40):
                    if "ready" in command(["docker", "logs", proxy], capture=True):
                        break
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Local Key Vault port adapter did not become ready")
            command(args, cwd=ROOT, timeout=600)
        finally:
            if proxy is not None:
                command(["docker", "rm", "-f", proxy])
        result = json.loads(report.read_text(encoding="utf-8-sig"))
        assert (
            result["passed"] and result["mode"] == mode and result["assertions"]["persistentRead"]
        )
        if mode == "write":
            assert all(
                result["assertions"][key]
                for key in (
                    "tableMutation",
                    "queueReceipts",
                    "secretRecovery",
                    "secretBackupRestore",
                )
            )
        if mode == "restore":
            assert result["assertions"]["secretBackupRestore"]
        results[ecosystem] = result
        check("structured_" + ecosystem + "_" + mode)
    return results


def cross_read(state, *, values_missing=False):
    from azure.core.credentials import AzureNamedKeyCredential
    from azure.core.exceptions import HttpResponseError
    from azure.core.pipeline.transport import RequestsTransport
    from azure.data.tables import TableClient
    from azure.keyvault.secrets import SecretClient
    from azure.storage.queue import QueueClient

    from integrations.python.arm import Credential

    common = json.loads((state / "python.json").read_text(encoding="utf-8"))
    checked = 0
    for ecosystem in ECOSYSTEMS:
        profile = json.loads((state / (ecosystem + ".json")).read_text(encoding="utf-8"))
        for service in ("table", "queue", "vault"):
            endpoint = (
                common[service + "_endpoint"]
                if service != "vault"
                else profile.get("host_vault_endpoint", profile["vault_endpoint"])
            )
            with LocalSession("https://" + urlsplit(endpoint).netloc, common["ca_file"]) as session:
                transport = RequestsTransport(
                    session=session, connection_verify=common["ca_file"], session_owner=False
                )
                if service == "table":
                    with TableClient(
                        endpoint,
                        ecosystem.capitalize() + "Persist",
                        credential=AzureNamedKeyCredential(common["account"], common["key"]),
                        transport=transport,
                        api_version="2019-02-02",
                        retry_total=0,
                    ) as native:
                        entity = native.get_entity("p", "one")
                        assert (
                            entity["Count"].value == 9007199254740993
                            and entity["Keep"]
                            and entity["Bytes"] == bytes([0, 1, 255])
                        )
                elif service == "queue":
                    with QueueClient(
                        endpoint,
                        ecosystem + "-persist",
                        credential=common["key"],
                        transport=transport,
                        api_version="2026-02-06",
                        retry_total=0,
                    ) as native:
                        assert native.peek_messages()[0].content == "persistent ☃"
                else:
                    with SecretClient(
                        endpoint,
                        Credential(common["token"]),
                        transport=transport,
                        api_version="2025-07-01",
                        verify_challenge_resource=False,
                        retry_total=0,
                    ) as native:
                        try:
                            result = native.get_secret(ecosystem + "-secret")
                        except HttpResponseError as error:
                            assert (
                                values_missing
                                and error.status_code == 409
                                and error.error.code == "SecretValueUnavailable"
                            )
                        else:
                            assert not values_missing and result.value == (
                                "two" if ecosystem == "python" else "second"
                            )
                checked += 1
    assert checked == 12
    return {"objects": checked, "valuesMissing": values_missing, "passed": True}


def qualify(directory, command, compose, client, headers, tenant, check):
    state = directory / ("structured-clients-" + uuid4().hex[:8])
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
            "display_name": "Structured native clients",
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
        tokens = {
            service: auth.acquire_token_for_client([audience + "/.default"])["access_token"]
            for service, audience in {
                "arm": "https://management.azure.com",
                "graph": "https://graph.microsoft.com",
                "vault": "https://vault.azure.net",
                "storage": "https://storage.azure.com",
            }.items()
        }
    principal = client.get(
        "/v1.0/servicePrincipals",
        params={"$filter": f"appId eq '{identifier}'"},
        headers={"Authorization": "Bearer " + tokens["graph"]},
    ).json()["value"][0]["id"]
    sub = request(
        "POST", base + "/subscriptions", {"display_name": "Structured workflow qualification"}, 201
    )["id"]
    subbase = base + "/subscriptions/" + sub

    def grant(scope, role):
        request(
            "PUT",
            subbase + "/role-assignments/" + str(uuid4()),
            {
                "principal_id": principal,
                "principal_type": "ServicePrincipal",
                "scope": scope,
                "role": role,
            },
        )

    grant("/subscriptions/" + sub, "Owner")
    bearer = {"Authorization": "Bearer " + tokens["arm"]}
    for namespace in ("Microsoft.Storage", "Microsoft.KeyVault"):
        response = client.post(
            "/subscriptions/" + sub + "/providers/" + namespace + "/register",
            params={"api-version": "2021-04-01"},
            headers=bearer,
        )
        assert response.status_code == 200
    instance = directory / ".runtime/instance/container.json"
    runtime = json.loads(instance.read_text(encoding="utf-8"))
    host = Settings.model_validate(runtime).endpoints.model_dump(mode="json")
    for name in ("storage", "file", "queue", "table", "keyvault"):
        host[name] = host[name].replace("https://localhost:", "https://127.0.0.1:")
    container = {
        name: "https://127.0.0.1:8443"
        for name in ("login", "graph", "exchange", "arm", "activity", "browser_login")
    }
    container.update(
        {
            name: f"https://127.0.0.1:{8444 + offset}"
            for offset, name in enumerate(("storage", "file", "queue", "table", "keyvault"))
        }
    )
    container["keyvault"] = "https://127.0.0.1:443"
    runtime["client_profiles"] = {"host": host, "container": container}
    runtime["keyvault_backup_key_file"] = "structured-backup.key"
    runtime["keyvault_backup_key_ref"] = "structured-qualification-v1"
    (instance.parent / "structured-backup.key").write_bytes(Fernet.generate_key())
    vault_names = {e: "native" + e[:4] + uuid4().hex[:8] for e in ECOSYSTEMS}
    ps_name = vault_names["powershell"]
    ps_host = ps_name + ".127-0-0-1.mockazure.test"
    runtime["allowed_hosts"] = list(dict.fromkeys([*runtime["allowed_hosts"], ps_host]))
    instance.write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")
    renew_leaf(instance.parent, tuple(runtime["allowed_hosts"]))
    (state / "hosts").write_text(
        "127.0.0.1 localhost " + ps_host + "\n::1 localhost\n", encoding="utf-8"
    )
    command(compose + ["restart", "api", "worker"])
    ready(client, [host[name] for name in ("table", "queue", "keyvault")])
    request("PUT", subbase + "/resource-groups/structured", {"location": "eastus"})
    group = "/subscriptions/" + sub + "/resourceGroups/structured"
    account = "native" + uuid4().hex[:12]
    account_id = group + "/providers/Microsoft.Storage/storageAccounts/" + account
    response = client.put(
        account_id,
        params={"api-version": "2025-06-01"},
        headers=bearer,
        json={"location": "eastus", "kind": "StorageV2", "sku": {"name": "Standard_LRS"}},
    )
    assert response.status_code == 200, response.text
    endpoints = response.json()["properties"]["primaryEndpoints"]
    response = client.post(
        account_id + "/listKeys", params={"api-version": "2025-06-01"}, headers=bearer
    )
    assert response.status_code == 200
    key = response.json()["keys"][0]["value"]
    grant(account_id, "Storage Table Data Contributor")
    for ecosystem in ECOSYSTEMS:
        name = vault_names[ecosystem]
        vault_id = group + "/providers/Microsoft.KeyVault/vaults/" + name
        response = client.put(
            vault_id,
            params={"api-version": "2025-05-01"},
            headers=bearer,
            json={
                "location": "eastus",
                "properties": {
                    "tenantId": tenant["id"],
                    "sku": {"family": "A", "name": "standard"},
                    "enableRbacAuthorization": True,
                    "accessPolicies": [],
                },
            },
        )
        assert response.status_code == 201, response.text
        grant(vault_id, "Key Vault Secrets Officer")
        profile = {
            "account": account,
            "key": key,
            "token": tokens["vault"],
            "storage_token": tokens["storage"],
            "arm_token": tokens["arm"],
            "client_id": identifier,
            "tenant_id": tenant["id"],
            "arm_endpoint": origin,
            "ca_file": ca,
            "vault_endpoint": response.json()["properties"]["vaultUri"],
            **{k + "_endpoint": v for k, v in endpoints.items()},
        }
        if ecosystem == "powershell":
            profile.update(
                ca_file="/certs/ca.pem",
                arm_endpoint="https://127.0.0.1:8443",
                vault_name=ps_name,
                vault_host=ps_host,
                vault_suffix="127-0-0-1.mockazure.test",
                vault_endpoint="https://" + ps_host,
                host_vault_endpoint=host["keyvault"].rstrip("/") + "/" + ps_name,
            )
            profile.update(
                {
                    service + "_endpoint": f"https://127.0.0.1:{8444 + offset}/{account}/"
                    for offset, service in enumerate(("blob", "file", "queue", "table"))
                }
            )
        (state / (ecosystem + ".json")).write_text(json.dumps(profile), encoding="utf-8")
    written = run_clients(directory, command, compose, state, check, "write")
    cross_before = cross_read(state)
    command(compose + ["restart", "api", "worker"])
    ready(client, [host[name] for name in ("table", "queue", "keyvault")])
    restarted = run_clients(directory, command, compose, state, check, "read")
    snapshot = request("GET", base + "/cloud/state")
    assert key not in json.dumps(snapshot)
    secret_rows = [v for secret in snapshot["keyvault"]["secrets"] for v in secret["versions"]]
    assert secret_rows and all(
        "value" not in row and "ciphertext" not in row for row in secret_rows
    )
    request("PUT", base + "/cloud/state", snapshot)
    response = client.post(
        account_id + "/listKeys", params={"api-version": "2025-06-01"}, headers=bearer
    )
    assert response.status_code == 200
    new_key = response.json()["keys"][0]["value"]
    assert new_key != key
    from azure.core.credentials import AzureNamedKeyCredential
    from azure.core.exceptions import HttpResponseError
    from azure.core.pipeline.transport import RequestsTransport
    from azure.data.tables import TableClient

    table_endpoint = endpoints["table"]
    with LocalSession("https://" + urlsplit(table_endpoint).netloc, ca) as transport:
        table = TableClient(
            table_endpoint,
            "PythonPersist",
            credential=AzureNamedKeyCredential(account, key),
            transport=RequestsTransport(session=transport, connection_verify=ca),
            api_version="2019-02-02",
            retry_total=0,
        )
        try:
            table.get_entity("p", "one")
        except HttpResponseError as error:
            assert error.status_code == 403
        else:
            raise AssertionError("Old account key remained authorized")
    for ecosystem in ECOSYSTEMS:
        path = state / (ecosystem + ".json")
        profile = json.loads(path.read_text(encoding="utf-8"))
        profile["key"] = new_key
        path.write_text(json.dumps(profile), encoding="utf-8")
    unavailable = cross_read(state, values_missing=True)
    restored = run_clients(directory, command, compose, state, check, "restore")
    cross_after = cross_read(state)
    result = {
        "passed": True,
        "localOnly": True,
        "written": written,
        "restarted": restarted,
        "restored": restored,
        "oldKeyDenied": True,
        "metadataOnlySnapshot": unavailable["passed"],
        "crossRead": {"before": cross_before, "after": cross_after},
    }
    (directory / "structured-services-report.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    from tools.prepare_structured_clients import prepare
    from tools.verify_default import main

    prepare()
    main(qualify=qualify)

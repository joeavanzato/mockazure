"""Original locked TypeScript/.NET SDKs against real CA-verified local HTTPS."""

import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import uvicorn
from cryptography.fernet import Fernet

from mockazure.admin.app import create_app
from mockazure.cloud.lifecycle import export_cloud, import_cloud
from mockazure.endpoints import EndpointProfile
from mockazure.identity.keys import read_wrapping_key
from mockazure.tls import ensure_tls
from tests.integration.graph_support import graph_client
from tests.integration.test_arm_authorization import assign
from tests.integration.test_keyvault_secrets import provision_vault
from tests.integration.test_storage_authorization import provision


@pytest.mark.skipif(
    os.environ.get("MOCKAZURE_NATIVE_STRUCTURED") != "1",
    reason="Run the explicit locked native-client qualification gate",
)
@pytest.mark.parametrize("ecosystem", ["python", "typescript", "dotnet"])
async def test_structured_original_clients_recovery(database, settings, tmp_path, ecosystem):
    root = Path(__file__).resolve().parents[2]
    listeners = []
    endpoints = settings.endpoints.model_dump()
    for service in ("table", "queue", "keyvault"):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        endpoints[service] = f"https://127.0.0.1:{listener.getsockname()[1]}"
        listeners.append(listener)
    key_file = settings.data_dir / "native-backup.key"
    key_file.write_bytes(Fernet.generate_key())
    local = settings.model_copy(
        update={
            "service_endpoints": EndpointProfile(**endpoints),
            "keyvault_backup_key_file": key_file,
            "keyvault_backup_key_ref": "native-key",
        }
    )
    tls = ensure_tls(local.data_dir, local.allowed_hosts)
    async with graph_client(database, local) as (client, tenant, app, user):
        account_id, arm, storage, key = await provision(client, database, local, tenant, app)
        vault, admin, principal, _, endpoint, head, _ = await provision_vault(
            client, database, local, tenant, app
        )
        await assign(client, admin, principal, vault, "Key Vault Secrets Officer")
        profile = {
            "table_endpoint": storage["table"],
            "queue_endpoint": storage["queue"],
            "vault_endpoint": endpoint,
            "account": "signedaccount",
            "key": key,
            "token": head["Authorization"][7:],
            "ca_file": str(tls.ca_cert),
        }
        profile_file = tmp_path / "profile.json"
        profile_file.write_text(json.dumps(profile), encoding="utf-8")
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(local, database),
                ssl_certfile=str(tls.server_cert),
                ssl_keyfile=str(tls.server_key),
                proxy_headers=False,
                access_log=False,
                log_level="error",
                lifespan="off",
                timeout_graceful_shutdown=2,
            )
        )
        task = asyncio.create_task(server.serve(sockets=listeners))
        try:
            for _ in range(200):
                if server.started:
                    break
                if task.done():
                    await task
                await asyncio.sleep(0.01)
            assert server.started
            entry = (
                [sys.executable, "-m", "integrations.python.structured_services"]
                if ecosystem == "python"
                else ["node", str(root / "work/cloud-d-clients/npm/runner.js")]
                if ecosystem == "typescript"
                else ["dotnet", str(root / "work/cloud-d-clients/dotnet/StructuredServices.dll")]
            )
            environment = os.environ.copy()
            environment["NODE_EXTRA_CA_CERTS"] = str(tls.ca_cert)
            environment["NO_PROXY"] = "*"
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                environment.pop(name, None)
            for mode in ("write", "read", "restore"):
                if mode == "restore":
                    async with database.sessions.begin() as session:
                        state = await export_cloud(session, tenant)
                        session.info["storage_cipher"] = read_wrapping_key(local.data_dir)
                        await import_cloud(session, tenant, state)
                    fresh = await client.post(
                        account_id + "/listKeys", params={"api-version": "2025-06-01"}, headers=arm
                    )
                    assert fresh.status_code == 200
                    profile["key"] = fresh.json()["keys"][0]["value"]
                    profile_file.write_text(json.dumps(profile), encoding="utf-8")
                report = tmp_path / (mode + "-report.json")
                process = await asyncio.to_thread(
                    subprocess.run,
                    [
                        *entry,
                        "--profile",
                        str(profile_file),
                        "--report",
                        str(report),
                        "--backup",
                        str(tmp_path / "secret.backup"),
                        "--mode",
                        mode,
                    ],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    timeout=120,
                )
                assert process.returncode == 0, (process.stdout + process.stderr).decode(
                    errors="replace"
                )[-10000:]
                result = json.loads(report.read_text(encoding="utf-8"))
                assert result["passed"] and result["assertions"]["persistentRead"]
                if mode == "write":
                    assert all(
                        result["assertions"][name]
                        for name in (
                            "tableMutation",
                            "queueReceipts",
                            "secretRecovery",
                            "secretBackupRestore",
                        )
                    )
                if mode == "restore":
                    assert result["assertions"]["secretBackupRestore"]
                print(ecosystem, mode, result)
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)
            for listener in listeners:
                listener.close()

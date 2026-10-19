"""Unmodified official Queue SDK sends signed HTTPS to the real local ASGI service."""

import asyncio
import socket

import uvicorn
from cryptography.fernet import Fernet

from integrations.python.keyvault_secrets import run_keyvault
from mockazure.admin.app import create_app
from mockazure.endpoints import EndpointProfile
from mockazure.tls import ensure_tls
from tests.integration.graph_support import graph_client
from tests.integration.test_arm_authorization import assign
from tests.integration.test_keyvault_secrets import provision_vault


async def test_original_python_keyvault_https(database, settings):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    profile = EndpointProfile(
        **(settings.endpoints.model_dump() | {"keyvault": f"https://127.0.0.1:{port}"})
    )
    backup_file = settings.data_dir / "sdk-backup.key"
    backup_file.write_bytes(Fernet.generate_key())
    local = settings.model_copy(
        update={
            "service_endpoints": profile,
            "keyvault_backup_key_file": backup_file,
            "keyvault_backup_key_ref": "sdk-key-v1",
        }
    )
    tls = ensure_tls(local.data_dir, local.allowed_hosts)
    async with graph_client(database, local) as (client, tenant, app, user):
        path, admin, principal, arm, endpoint, head, policy = await provision_vault(
            client, database, local, tenant, app
        )
        await assign(client, admin, principal, path, "Key Vault Secrets Officer")
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(local, database),
                host="127.0.0.1",
                port=port,
                ssl_certfile=str(tls.server_cert),
                ssl_keyfile=str(tls.server_key),
                proxy_headers=False,
                access_log=False,
                log_level="error",
                lifespan="off",
                timeout_graceful_shutdown=2,
            )
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            for _ in range(200):
                if server.started:
                    break
                if task.done():
                    await task
                await asyncio.sleep(0.01)
            assert server.started
            result = await asyncio.to_thread(
                run_keyvault,
                {
                    "vault_endpoint": endpoint,
                    "recovery": True,
                    "account": "signedaccount",
                    "token": head["Authorization"][7:],
                    "ca_file": str(tls.ca_cert),
                },
            )
            assert result["passed"] and result["challengeScopeVerified"]
            assert result["recovery"] is True
            print(result)
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)
            listener.close()

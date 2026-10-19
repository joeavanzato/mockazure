"""Unmodified official Queue SDK sends signed HTTPS to the real local ASGI service."""

import asyncio
import socket

import uvicorn

from integrations.python.storage_structured import run_queue
from mockazure.admin.app import create_app
from mockazure.endpoints import EndpointProfile
from mockazure.tls import ensure_tls
from tests.integration.graph_support import graph_client
from tests.integration.test_storage_authorization import provision


async def test_original_python_queue_https(database, settings):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    profile = EndpointProfile(
        **(settings.endpoints.model_dump() | {"queue": f"https://127.0.0.1:{port}"})
    )
    local = settings.model_copy(update={"service_endpoints": profile})
    tls = ensure_tls(local.data_dir, local.allowed_hosts)
    async with graph_client(database, local) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, local, tenant, app)
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
                run_queue,
                {
                    "queue_endpoint": endpoints["queue"],
                    "account": "signedaccount",
                    "key": key,
                    "ca_file": str(tls.ca_cert),
                },
            )
            assert result["passed"] and result["queue"]["rawAndBase64"]
            print(result)
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)
            listener.close()

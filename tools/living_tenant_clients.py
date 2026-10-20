"""Original client collection gates for the living-tenant qualification tool."""

import asyncio
import hashlib
import json
import os
import shutil
import ssl
import subprocess
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import msal

from integrations.python.arm import LocalSession
from integrations.python.graph_directory import graph_client
from mockazure.endpoints import local_origin


def verify_exchange_client():
    from tools.prepare_clients import prepare

    pinned = json.loads(Path("integrations/manifest.json").read_text(encoding="utf-8"))
    required = {
        "ExchangeOnlineManagement",
        "PowerShellGet",
        "PackageManagement",
        "Microsoft.Extensions.DependencyInjection.Abstractions",
    }
    pinned["artifacts"] = [a for a in pinned["artifacts"] if a["name"] in required]
    path = Path("work/living-exchange-manifest.json")
    path.write_text(json.dumps(pinned), encoding="utf-8")
    prepare(path, Path("work/living-clients").resolve(), verify_only=True)


def acquire(settings, ctx, credential, audience):
    with LocalSession(local_origin(settings.endpoints.login), str(settings.ca_file)) as session:
        app = msal.ConfidentialClientApplication(
            str(credential.client_id),
            client_credential=credential.secret.get_secret_value(),
            authority=settings.endpoints.login + "/" + str(ctx.tenant_id),
            instance_discovery=False,
            validate_authority=False,
            http_client=session,
        )
        result = app.acquire_token_for_client([audience + "/.default"])
        if "access_token" not in result:
            raise RuntimeError("Local Microsoft client authentication failed")
        return result["access_token"]


def storage_read(settings, refs, key):
    from azure.core.credentials import AzureNamedKeyCredential
    from azure.core.pipeline.transport import RequestsTransport
    from azure.data.tables import TableServiceClient
    from azure.storage.blob import BlobServiceClient
    from azure.storage.fileshare import ShareServiceClient
    from azure.storage.queue import QueueServiceClient

    account = refs["account"].rsplit("/", 1)[1]
    data, result = {}, {}
    for service, cls, package in (
        ("storage", BlobServiceClient, "azure-storage-blob"),
        ("file", ShareServiceClient, "azure-storage-file-share"),
        ("queue", QueueServiceClient, "azure-storage-queue"),
        ("table", TableServiceClient, "azure-data-tables"),
    ):
        endpoint = local_origin(getattr(settings.endpoints, service))
        with LocalSession(endpoint, str(settings.ca_file)) as session:
            credential = (
                AzureNamedKeyCredential(account, key)
                if service == "table"
                else {"account_name": account, "account_key": key}
            )
            kwargs = {
                "credential": credential,
                "transport": RequestsTransport(
                    session=session, connection_verify=str(settings.ca_file)
                ),
                "retry_total": 0,
                "api_version": (
                    "2019-02-02"
                    if service == "table"
                    else "2026-02-06"
                    if service == "queue"
                    else "2025-11-05"
                ),
            }
            with cls(endpoint + "/" + account, **kwargs) as client:
                if service == "storage":
                    data[service] = (
                        client.get_blob_client("evidence", "project-review.docx")
                        .download_blob()
                        .readall()
                    )
                elif service == "file":
                    data[service] = (
                        client.get_share_client("evidence")
                        .get_file_client("project-review.docx")
                        .download_file()
                        .readall()
                    )
                elif service == "queue":
                    messages = list(
                        client.get_queue_client("evidence").peek_messages(max_messages=32)
                    )
                    assert len(messages) == 1 and messages[0].content.startswith(
                        "Synthetic queue item "
                    )
                else:
                    rows = list(client.get_table_client("Evidence").list_entities())
                    assert (
                        len(rows) == 1 and rows[0]["RowKey"] == "example" and rows[0]["Count"] == 1
                    )
                result[service] = {
                    "state": "passed",
                    "client": package,
                    "version": version(package),
                }
    assert data["storage"] == data["file"] and data["storage"].startswith(b"PK")
    for name in data:
        result[name].update(bytes=len(data[name]), sha256=hashlib.sha256(data[name]).hexdigest())
    return result


async def collect(
    settings, admin, ctx, credential, transport, refs, entities, domain, expected_users
):
    await asyncio.to_thread(verify_exchange_client)
    raw = await asyncio.to_thread(acquire, settings, ctx, credential, "https://graph.microsoft.com")
    async with httpx.AsyncClient(
        verify=ssl.create_default_context(cafile=str(settings.ca_file)),
        trust_env=False,
        follow_redirects=False,
        timeout=60,
    ) as http:
        graph = graph_client({"endpoints": {"graph": settings.endpoints.graph}}, raw, http)
        users, page = [], await graph.users.get()
        while True:
            users.extend(page.value)
            if not page.odata_next_link:
                break
            assert (
                urlsplit(page.odata_next_link).netloc == urlsplit(settings.endpoints.graph).netloc
            )
            page = await graph.users.with_url(page.odata_next_link).get()
        expected = {value for key, value in entities.items() if key.startswith("business.users:")}
        assert {user.id for user in users} == expected and len(users) == expected_users
        devices = (await graph.device_management.managed_devices.get()).value
        assert devices and {device.user_id for device in devices} <= expected
        drive_id = entities["business.drives:0"]
        drive = await graph.drives.by_drive_id(drive_id).get()
        assert drive.id == drive_id
        document_id = entities["business.documents:0"]
        document = (
            await graph.drives.by_drive_id(drive_id).items.by_drive_item_id(document_id).get()
        )
        assert document.id == document_id and document.size > 0
        messages = await graph.users.by_user_id(refs["primary-user"]).messages.get()
        assert messages.value
        message = (
            await graph.users.by_user_id(refs["primary-user"])
            .messages.by_message_id(messages.value[0].id)
            .get()
        )
        assert message.id == messages.value[0].id and message.body.content
    result = {
        "graph": {
            "state": "passed",
            "client": "msgraph-sdk",
            "version": version("msgraph-sdk"),
            "users": len(users),
            "drive_id": drive_id,
            "document_id": document_id,
        }
    }
    keys = await transport.request(
        "arm", "POST", refs["account"] + "/listKeys", params={"api-version": "2025-06-01"}, json={}
    )
    assert keys.status_code == 200
    result.update(
        await asyncio.to_thread(storage_read, settings, refs, keys.json()["keys"][0]["value"])
    )
    base = f"/_mock/tenants/{ctx.tenant_id}/identity"
    from uuid import UUID

    from pydantic import SecretStr

    from mockazure.population.transport import ClientCredential

    permission = {
        "resource": "exchange",
        "kind": "application",
        "permissions": ["Exchange.ManageAsApp"],
    }
    exchange_app = await admin.request(
        "POST",
        base + "/applications",
        json={
            "display_name": "Living tenant Exchange collector",
            "client_mode": "confidential",
            "required_permissions": [permission],
        },
    )
    exchange_id = exchange_app["client_id"]
    secret = await admin.request(
        "POST", base + f"/applications/{exchange_id}/credentials", json={"kind": "secret"}
    )
    exchange_credential = ClientCredential(UUID(exchange_id), SecretStr(secret["secret"]))
    await admin.request(
        "PUT",
        base + "/grants/" + str(uuid4()),
        json={
            "client_id": exchange_id,
            **permission,
        },
    )
    await admin.request(
        "PUT", base + f"/applications/{exchange_id}/roles/Exchange Administrator", json={}
    )
    exchange_token = await asyncio.to_thread(
        acquire, settings, ctx, exchange_credential, "https://outlook.office365.com"
    )
    profile = {
        "endpoint": settings.endpoints.exchange,
        "login": settings.endpoints.login,
        "client_id": exchange_id,
        "domain": domain,
        "token": exchange_token,
        "ca_file": str(settings.ca_file),
        "expected_mailboxes": expected_users,
        "expected_user_ids": sorted(expected),
        "mailbox_upn": users[0].user_principal_name,
        "mailbox_user_id": users[0].id,
        "relay": False,
        "vendor": str(Path("work/living-clients").resolve()),
        "logs": str(settings.data_dir / "exo-qualification"),
    }
    command = [
        shutil.which("pwsh") or "pwsh",
        "-NoProfile",
        "-File",
        str(Path("integrations/powershell/Test-LivingTenant.ps1").resolve()),
    ]
    client_environment = dict(os.environ, SSL_CERT_FILE=str(settings.ca_file))
    runtime = "native"
    if os.name == "nt":
        from tools.verify_collections import PS_IMAGE

        vendor = Path("work/living-clients").resolve()
        script = Path("integrations/powershell/Test-LivingTenant.ps1").resolve()
        log_dir = settings.data_dir / "exo-qualification"
        log_dir.mkdir(exist_ok=True)
        profile.update(ca_file="/lab/ca.pem", vendor="/vendor", logs="/state", relay=True)
        command = [
            shutil.which("docker") or "docker",
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
            f"type=bind,source={script},target=/lab/Test-LivingTenant.ps1,readonly",
            "--mount",
            f"type=bind,source={log_dir},target=/state",
            PS_IMAGE,
            "pwsh",
            "-NoProfile",
            "-File",
            "/lab/Test-LivingTenant.ps1",
        ]
        runtime = "isolated-linux-client"
    process = await asyncio.to_thread(
        subprocess.run,
        command,
        input=json.dumps(profile),
        text=True,
        capture_output=True,
        timeout=180,
        env=client_environment,
    )
    if process.returncode:
        safe_error = process.stderr.replace(exchange_token, "[redacted]")
        (settings.data_dir / "exchange-qualification-error.log").write_text(
            safe_error, encoding="utf-8"
        )
        raise RuntimeError(
            "Original Exchange client qualification failed; no token output retained"
        )
    result["exchange"] = json.loads(process.stdout[process.stdout.index('{"state"') :].strip())
    result["exchange"]["runtime"] = runtime
    return result

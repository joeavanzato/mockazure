"""Managed-identity administration; one-time host credentials go to exclusive private files."""

import os
from pathlib import Path
from typing import Literal
from uuid import UUID

import typer

from mockazure.commands.tenants import DEFAULT_CONFIG, execute
from mockazure.config import load_settings

app = typer.Typer()


@app.command("serve")
def serve(config: Path = typer.Option(DEFAULT_CONFIG)):
    import uvicorn

    from mockazure.asyncio_support import run
    from mockazure.managed_identity.app import create_managed_identity_app

    try:
        settings = load_settings(config)
        server = uvicorn.Server(
            uvicorn.Config(
                create_managed_identity_app(settings),
                host=settings.managed_identity_listen_host,
                port=settings.managed_identity_listen_port,
                proxy_headers=False,
                access_log=False,
                log_level="warning",
            )
        )
        run(server.serve())
    except (ValueError, OSError):
        typer.echo("Managed identity startup failed; check the local configuration", err=True)
        raise typer.Exit(2) from None


@app.command("create")
def create(
    tenant_id: UUID = typer.Option(..., "--tenant-id"),
    display_name: str = typer.Option(..., "--display-name"),
    kind: Literal["system", "user"] = typer.Option(..., "--kind"),
    resource_id: str = typer.Option(..., "--resource-id"),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    execute(
        config,
        lambda client: client.request(
            "POST",
            f"/_mock/tenants/{tenant_id}/managed-identities",
            json={"display_name": display_name, "kind": kind, "resource_id": resource_id},
        ),
    )


def provision_header(config, output, path, body=None):
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        raise typer.BadParameter("Header output must be a new writable file") from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:

        async def operation(client):
            response = await client.request(
                "POST", path, **({"json": body} if body is not None else {})
            )
            secret = response.pop("header")
            stream.write(secret + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            return {**response, "header_file": str(output)}

        execute(config, operation)


@app.command("create-host")
def create_host(
    tenant_id: UUID = typer.Option(..., "--tenant-id"),
    name: str = typer.Option(..., "--name"),
    header_file: Path = typer.Option(..., "--output", "--header-file"),
    system_identity_id: UUID | None = typer.Option(None, "--system-identity-id"),
    user_identity_id: list[UUID] = typer.Option([], "--user-identity-id"),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    provision_header(
        config,
        header_file,
        f"/_mock/tenants/{tenant_id}/managed-identity-hosts",
        {
            "name": name,
            "system_identity_id": str(system_identity_id) if system_identity_id else None,
            "user_identity_ids": list(map(str, user_identity_id)),
        },
    )


@app.command("rotate-header")
def rotate_header(
    tenant_id: UUID = typer.Option(..., "--tenant-id"),
    host_id: UUID = typer.Option(..., "--host-id"),
    header_file: Path = typer.Option(..., "--output", "--header-file"),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    provision_header(
        config,
        header_file,
        f"/_mock/tenants/{tenant_id}/managed-identity-hosts/{host_id}/rotate-header",
    )


@app.command("enable")
def enable(
    tenant_id: UUID = typer.Option(..., "--tenant-id"),
    identity_id: UUID = typer.Option(..., "--identity-id"),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    execute(
        config,
        lambda client: client.request(
            "PATCH",
            f"/_mock/tenants/{tenant_id}/managed-identities/{identity_id}",
            json={"enabled": enabled},
        ),
    )


@app.command("update-host")
def update_host(
    tenant_id: UUID = typer.Option(..., "--tenant-id"),
    host_id: UUID = typer.Option(..., "--host-id"),
    input_file: Path = typer.Option(..., "--input"),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    import json

    from mockazure.managed_identity.schemas import HostUpdate

    try:
        value = HostUpdate.model_validate(json.loads(input_file.read_text()))
    except (ValueError, OSError):
        raise typer.BadParameter("Invalid host update file") from None
    execute(config, lambda client: client.update_managed_host(tenant_id, host_id, value))

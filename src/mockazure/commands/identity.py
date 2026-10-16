"""Identity administration over the same authenticated HTTPS control plane."""

import json
import os
from pathlib import Path
from typing import Literal
from uuid import UUID

import typer

from mockazure.commands.tenants import DEFAULT_CONFIG, execute
from mockazure.identity.types import ApplicationCreate, GrantCreate, UserCreate

app = typer.Typer()


def input_data(path: Path, schema):
    try:
        if path.stat().st_size > 65536:
            raise ValueError()
        return schema.model_validate_json(path.read_text(encoding="utf-8")).model_dump(mode="json")
    except (ValueError, OSError):
        raise typer.BadParameter("Invalid identity input file") from None


@app.command("create-user")
def create_user(
    tenant_id: UUID, input: Path = typer.Option(...), config: Path = typer.Option(DEFAULT_CONFIG)
):
    # SecretStr intentionally requires explicit extraction for the HTTPS request.
    try:
        if input.stat().st_size > 65536:
            raise ValueError()
        user = UserCreate.model_validate_json(input.read_text(encoding="utf-8"))
        data = user.model_dump(mode="json")
        data["password"] = user.password.get_secret_value()
    except (ValueError, OSError):
        raise typer.BadParameter("Invalid user input file") from None
    execute(
        config,
        lambda client: client.request(
            "POST", f"/_mock/tenants/{tenant_id}/identity/users", json=data
        ),
    )


@app.command("create-application")
def create_application(
    tenant_id: UUID, input: Path = typer.Option(...), config: Path = typer.Option(DEFAULT_CONFIG)
):
    data = input_data(input, ApplicationCreate)
    execute(
        config,
        lambda client: client.request(
            "POST", f"/_mock/tenants/{tenant_id}/identity/applications", json=data
        ),
    )


@app.command("list")
def list_command(
    tenant_id: UUID,
    collection: Literal["users", "applications"],
    limit: int = 100,
    after: UUID | None = None,
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    params = {"limit": limit}
    if after:
        params["after"] = str(after)
    execute(
        config,
        lambda client: client.request(
            "GET", f"/_mock/tenants/{tenant_id}/identity/{collection}", params=params
        ),
    )


@app.command("issue-secret")
def issue_secret(
    tenant_id: UUID,
    client_id: UUID,
    output: Path = typer.Option(...),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    # Reserve exclusively before issuing anything, so existing output cannot be overwritten.
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        raise typer.BadParameter("Output must be a new writable file") from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:

        async def operation(client):
            result = await client.request(
                "POST",
                f"/_mock/tenants/{tenant_id}/identity/applications/{client_id}/credentials",
                json={"kind": "secret"},
            )
            json.dump(result, stream)
            stream.flush()
            os.fsync(stream.fileno())
            return {key: value for key, value in result.items() if key != "secret"}

        execute(config, operation)


@app.command("register-certificate")
def register_certificate(
    tenant_id: UUID,
    client_id: UUID,
    certificate: Path = typer.Option(...),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    try:
        if certificate.stat().st_size > 16384:
            raise ValueError()
        value = certificate.read_text(encoding="ascii")
    except (ValueError, OSError):
        raise typer.BadParameter("Invalid public certificate file") from None
    execute(
        config,
        lambda client: client.request(
            "POST",
            f"/_mock/tenants/{tenant_id}/identity/applications/{client_id}/credentials",
            json={"kind": "certificate", "public_certificate": value},
        ),
    )


@app.command("revoke-credential")
def revoke_credential(
    tenant_id: UUID, credential_id: UUID, config: Path = typer.Option(DEFAULT_CONFIG)
):
    execute(
        config,
        lambda client: client.request(
            "DELETE", f"/_mock/tenants/{tenant_id}/identity/credentials/{credential_id}"
        ),
    )


@app.command("set-grant")
def set_grant(
    tenant_id: UUID,
    grant_id: UUID,
    input: Path = typer.Option(...),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    data = input_data(input, GrantCreate)
    execute(
        config,
        lambda client: client.request(
            "PUT", f"/_mock/tenants/{tenant_id}/identity/grants/{grant_id}", json=data
        ),
    )


@app.command("revoke-grant")
def revoke_grant(tenant_id: UUID, grant_id: UUID, config: Path = typer.Option(DEFAULT_CONFIG)):
    execute(
        config,
        lambda client: client.request(
            "DELETE", f"/_mock/tenants/{tenant_id}/identity/grants/{grant_id}"
        ),
    )


@app.command("set-enabled")
def set_enabled(
    tenant_id: UUID,
    collection: Literal["users", "applications"],
    object_id: UUID,
    enabled: bool,
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    execute(
        config,
        lambda client: client.request(
            "PATCH",
            f"/_mock/tenants/{tenant_id}/identity/{collection}/{object_id}",
            json={"enabled": enabled},
        ),
    )

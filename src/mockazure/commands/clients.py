import json
from pathlib import Path
from typing import Literal
from uuid import UUID

import typer

from mockazure.commands.tenants import DEFAULT_CONFIG
from mockazure.config import load_settings
from mockazure.endpoints import RESOURCES
from mockazure.files import write_private


def client_config(
    tenant: UUID = typer.Option(...),
    client: UUID = typer.Option(...),
    output: Path = typer.Option(...),
    profile: Literal["host", "container"] = "host",
    mode: Literal["Application", "Device", "AuthorizationCode"] = "Application",
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    """Export non-secret endpoint configuration for a registered local client."""
    try:
        settings = load_settings(config)
        endpoints = settings.client_profiles.get(profile)
        if endpoints is None:
            if profile != "host":
                raise ValueError("Container profile is not configured")
            endpoints = settings.endpoints
        result = {
            "schema_version": 1,
            "endpoints": endpoints.model_dump(),
            "issuer": settings.endpoints.issuer(str(tenant)),
            "tenant_id": str(tenant),
            "client_id": str(client),
            "authentication_mode": mode,
            "resource_audiences": {r.service: r.audience for r in RESOURCES},
            "ca_file": str(settings.ca_file),
            "limitations": ["CA path is host-local; mount its public PEM for container callers."],
        }
        write_private(output, (json.dumps(result, indent=2) + "\n").encode())
        typer.echo(f"Client configuration written to {output}")
    except (ValueError, OSError):
        raise typer.BadParameter(
            "Cannot export client configuration; check profile and output"
        ) from None

import json
from pathlib import Path
from uuid import UUID

import typer
from pydantic import BaseModel

from mockazure.admin.client import AdminClient, AdminClientError
from mockazure.asyncio_support import run
from mockazure.config import load_settings
from mockazure.tenants.schemas import TenantCreate

app = typer.Typer()
DEFAULT_CONFIG = Path(".runtime/instance/native.json")


def execute(config, operation):
    async def invoke():
        settings = load_settings(config)
        async with AdminClient(
            str(settings.admin_base_url), ca_file=settings.ca_file, token_file=settings.token_file
        ) as client:
            return await operation(client)

    try:
        result = run(invoke())
        typer.echo(
            result.model_dump_json() if isinstance(result, BaseModel) else json.dumps(result)
        )
    except (AdminClientError, ValueError, OSError):
        typer.echo(
            "Management command failed; check inputs, service, credentials and CA.", err=True
        )
        raise typer.Exit(1) from None


@app.command("create")
def create(
    slug: str = typer.Option(...),
    display_name: str = typer.Option(...),
    domain: str = typer.Option(...),
    licence: list[str] = typer.Option(None),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    try:
        data = TenantCreate(
            slug=slug, display_name=display_name, domain=domain, licences=licence or []
        )
    except ValueError:
        raise typer.BadParameter("Invalid tenant fields") from None
    execute(config, lambda client: client.create_tenant(data))


@app.command("list")
def list_command(
    config: Path = typer.Option(DEFAULT_CONFIG), after: UUID | None = None, limit: int = 100
):
    execute(config, lambda client: client.list_tenants(after, limit))


@app.command("show")
def show(tenant_id: UUID, config: Path = typer.Option(DEFAULT_CONFIG)):
    execute(config, lambda client: client.get_tenant(tenant_id))


def capabilities(config: Path = typer.Option(DEFAULT_CONFIG)):
    execute(config, lambda client: client.request("GET", "/_mock/capabilities"))


def health(config: Path = typer.Option(DEFAULT_CONFIG)):
    execute(config, lambda client: client.request("GET", "/_mock/health"))

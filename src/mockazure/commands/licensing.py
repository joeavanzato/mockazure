from pathlib import Path
from uuid import UUID

import typer

from mockazure.commands.tenants import DEFAULT_CONFIG, execute

app = typer.Typer()


@app.command("put-sku")
def put_sku(
    tenant_id: UUID = typer.Option(..., "--tenant-id"),
    sku_id: UUID = typer.Option(..., "--sku-id"),
    file: Path = typer.Option(..., "--file"),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    try:
        with file.open("rb") as source:
            data = source.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError()
    except (OSError, ValueError):
        raise typer.BadParameter("Inventory file is unavailable or exceeds1MiB") from None
    execute(
        config,
        lambda client: client.request(
            "PUT",
            f"/_mock/tenants/{tenant_id}/licensing/skus/{sku_id}",
            content=data,
            headers={"Content-Type": "application/json"},
        ),
    )


@app.command("list")
def list_skus(
    tenant_id: UUID = typer.Option(..., "--tenant-id"), config: Path = typer.Option(DEFAULT_CONFIG)
):
    execute(
        config, lambda client: client.request("GET", f"/_mock/tenants/{tenant_id}/licensing/skus")
    )

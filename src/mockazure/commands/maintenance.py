"""Run bounded local maintenance through the authenticated HTTPS API."""

from pathlib import Path
from uuid import UUID

import typer

from mockazure.commands.tenants import DEFAULT_CONFIG, execute

app = typer.Typer()


@app.command("cleanup")
def cleanup(
    tenant_id: UUID = typer.Option(...),
    max_rows: int = typer.Option(500, min=1, max=1000),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    execute(
        config,
        lambda client: client.request(
            "POST",
            f"/_mock/tenants/{tenant_id}/maintenance/cleanup",
            json={"max_rows": max_rows},
        ),
    )

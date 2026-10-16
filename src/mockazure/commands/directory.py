"""Public dataset commands; never restore credentials or sessions."""

from pathlib import Path
from uuid import UUID

import httpx
import typer

from mockazure.admin.client import AdminClientError
from mockazure.commands.tenants import DEFAULT_CONFIG, execute
from mockazure.directory.datasets import MAX_BYTES

app = typer.Typer()


@app.command("import")
def import_file(
    tenant_id: UUID = typer.Option(..., "--tenant-id"),
    file: Path = typer.Option(..., "--file"),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    try:
        with file.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError()
    except (OSError, ValueError):
        raise typer.BadParameter("Dataset is unavailable or exceeds 10 MiB") from None
    execute(
        config,
        lambda client: client.request(
            "POST",
            f"/_mock/tenants/{tenant_id}/directory/import",
            content=data,
            headers={"Content-Type": "application/json"},
        ),
    )


@app.command("export")
def export_file(
    tenant_id: UUID = typer.Option(..., "--tenant-id"),
    output: Path = typer.Option(..., "--output"),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    async def operation(client):
        created = False
        try:
            async with client.http.stream(
                "GET",
                f"/_mock/tenants/{tenant_id}/directory/export",
                timeout=120,
            ) as response:
                if response.status_code != 200:
                    raise AdminClientError("Directory export failed")
                with output.open("xb") as destination:
                    created = True
                    async for chunk in response.aiter_bytes():
                        destination.write(chunk)
        except (OSError, httpx.HTTPError):
            if created:
                output.unlink(missing_ok=True)
            raise AdminClientError("Directory export could not be saved") from None
        return {"output": str(output), "schema_version": 1}

    execute(config, operation)


@app.command("cleanup")
def cleanup(
    tenant_id: UUID = typer.Option(..., "--tenant-id"),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    execute(
        config,
        lambda client: client.request("POST", f"/_mock/tenants/{tenant_id}/directory/cleanup"),
    )

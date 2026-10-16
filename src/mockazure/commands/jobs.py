import asyncio
import time
from pathlib import Path
from uuid import UUID

import typer

from mockazure.admin.client import AdminClientError
from mockazure.asyncio_support import run
from mockazure.commands.tenants import DEFAULT_CONFIG, execute
from mockazure.config import load_settings
from mockazure.db.engine import close_database, create_database
from mockazure.db.migrations import schema_is_current
from mockazure.jobs.schemas import ExportSnapshot
from mockazure.jobs.worker import serve_worker
from mockazure.tenants.schemas import TenantCreate

app = typer.Typer()


@app.command("show")
def show(tenant_id: UUID, job_id: UUID, config: Path = typer.Option(DEFAULT_CONFIG)):
    execute(config, lambda client: client.get_job(tenant_id, job_id))


def export(
    tenant_id: UUID,
    config: Path = typer.Option(DEFAULT_CONFIG),
    wait: bool = False,
    timeout: float = typer.Option(60, min=0.1, max=3600),
    output: Path | None = None,
):
    async def operation(client):
        job = await client.enqueue_export(tenant_id)
        if not wait and output is None:
            return job
        deadline = time.monotonic() + timeout
        while job.state in ("pending", "running"):
            if time.monotonic() >= deadline:
                raise AdminClientError("Export wait timed out")
            await asyncio.sleep(0.25)
            job = await client.get_job(tenant_id, job.id)
        if job.state != "succeeded":
            raise AdminClientError("Export failed")
        snapshot = await client.get_export_result(tenant_id, job.id)
        if output is not None:
            # Explicit file output never overwrites an existing export.
            with output.open("x", encoding="utf-8") as stream:
                stream.write(snapshot.model_dump_json(indent=2) + "\n")
        return snapshot

    execute(config, operation)


def import_file(
    file: Path,
    config: Path = typer.Option(DEFAULT_CONFIG),
    slug: str | None = None,
    domain: str | None = None,
    display_name: str | None = None,
):
    try:
        snapshot = ExportSnapshot.model_validate_json(file.read_text())
        replacement = None
        if any(value is not None for value in (slug, domain, display_name)):
            values = snapshot.tenant.model_dump()
            values.update(
                {
                    key: value
                    for key, value in {
                        "slug": slug,
                        "domain": domain,
                        "display_name": display_name,
                    }.items()
                    if value is not None
                }
            )
            replacement = TenantCreate(**values)
    except (ValueError, OSError):
        raise typer.BadParameter("Invalid export file or replacement metadata") from None
    execute(config, lambda client: client.import_tenant(snapshot, replacement))


def worker(config: Path = typer.Option(DEFAULT_CONFIG)):
    async def loop():
        database = create_database(load_settings(config))
        try:
            if not await schema_is_current(database):
                raise ValueError("Database is not ready")
            await serve_worker(database)
        finally:
            await close_database(database)

    try:
        run(loop())
    except (ValueError, OSError):
        typer.echo("Worker startup failed; check configuration, database and migrations.", err=True)
        raise typer.Exit(2) from None

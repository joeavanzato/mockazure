"""Evidence administration is an HTTPS API client, with no direct database access."""

import os
from pathlib import Path
from uuid import UUID

import typer

from mockazure.commands.tenants import DEFAULT_CONFIG, execute

app = typer.Typer()


def path(tenant_id, suffix):
    return f"/_mock/tenants/{tenant_id}/evidence/" + suffix


def input_bytes(file, limit):
    try:
        with file.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError()
        return data
    except (OSError, ValueError):
        raise typer.BadParameter("Input file is unavailable or exceeds the local limit") from None


@app.command("state")
def state(tenant_id: UUID = typer.Option(...), config: Path = typer.Option(DEFAULT_CONFIG)):
    execute(config, lambda client: client.evidence_state(tenant_id))


@app.command("settings")
def settings(
    tenant_id: UUID = typer.Option(...),
    input_file: Path | None = typer.Option(None, "--input"),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    if input_file is None:
        execute(config, lambda client: client.evidence_settings(tenant_id))
    else:
        data = input_bytes(input_file, 1024 * 1024)
        execute(
            config,
            lambda client: client.request(
                "PATCH",
                path(tenant_id, "settings"),
                content=data,
                headers={"Content-Type": "application/json"},
            ),
        )


@app.command("clock")
def clock(
    tenant_id: UUID = typer.Option(...),
    seconds: int = typer.Option(..., min=0, max=31536000),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    execute(
        config,
        lambda client: client.advance_evidence_clock(tenant_id, seconds),
    )


@app.command("drain")
def drain(
    tenant_id: UUID = typer.Option(...),
    max_jobs: int = typer.Option(100, min=1, max=1000),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    execute(
        config,
        lambda client: client.drain_evidence(tenant_id, max_jobs),
    )


@app.command("cleanup")
def cleanup(
    tenant_id: UUID = typer.Option(...),
    max_rows: int = typer.Option(500, min=1, max=1000),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    execute(
        config,
        lambda client: client.cleanup_evidence(tenant_id, max_rows),
    )


@app.command("reset")
def reset(
    tenant_id: UUID = typer.Option(...),
    expected_generation: UUID = typer.Option(...),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    execute(
        config,
        lambda client: client.reset_evidence(tenant_id, expected_generation),
    )


@app.command("jobs")
def jobs(
    tenant_id: UUID = typer.Option(...),
    state: str | None = typer.Option(None),
    limit: int = typer.Option(50, min=1, max=100),
    continuation: str | None = typer.Option(None),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    params = (
        {"continuation": continuation}
        if continuation
        else {"limit": limit, **({"state": state} if state else {})}
    )
    execute(config, lambda client: client.request("GET", path(tenant_id, "jobs"), params=params))


@app.command("retry")
def retry(
    tenant_id: UUID = typer.Option(...),
    job_id: UUID = typer.Option(...),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    execute(config, lambda client: client.retry_evidence_job(tenant_id, job_id))


@app.command("import")
def import_records(
    tenant_id: UUID = typer.Option(...),
    input_file: Path = typer.Option(..., "--input"),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    data = input_bytes(input_file, 10 * 1024 * 1024)
    execute(
        config,
        lambda client: client.import_evidence(tenant_id, data),
    )


@app.command("export")
def export_records(
    tenant_id: UUID = typer.Option(...),
    output: Path = typer.Option(...),
    family: str | None = typer.Option(None),
    from_time: str | None = typer.Option(None, "--from"),
    to_time: str | None = typer.Option(None, "--to"),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        raise typer.BadParameter("Output must be a new writable file") from None
    with os.fdopen(descriptor, "wb") as destination:

        async def operation(client):
            async for chunk in client.export_evidence(
                tenant_id, family=family, from_time=from_time, to_time=to_time
            ):
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
            return {"output": str(output)}

        execute(config, operation)


@app.command("observe-risk")
def observe_risk(
    tenant_id: UUID = typer.Option(...),
    input_file: Path = typer.Option(..., "--input"),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    data = input_bytes(input_file, 1024 * 1024)
    execute(
        config,
        lambda client: client.observe_risk(tenant_id, data),
    )

"""Population CLI: the same authenticated APIs used by the browser console."""

import asyncio
import json
from pathlib import Path
from time import monotonic
from uuid import UUID

import typer

from mockazure.admin.client import AdminClient, AdminClientError
from mockazure.asyncio_support import run
from mockazure.commands.profiles import read_bundle
from mockazure.commands.tenants import DEFAULT_CONFIG
from mockazure.config import load_settings
from mockazure.profiles.loader import MAX_DOCUMENT_BYTES, parse_document

app = typer.Typer(no_args_is_help=True)


def read_object(path):
    with path.open("rb") as stream:
        return parse_document(stream.read(MAX_DOCUMENT_BYTES + 1))[0]


def output_result(value, output=None):
    text = json.dumps(value, indent=2, default=str) + "\n"
    if output:
        with output.open("x", encoding="utf-8") as destination:
            destination.write(text)
    else:
        typer.echo(text.rstrip())


def execute(config, operation, output=None):
    async def invoke():
        settings = load_settings(config)
        async with AdminClient(
            str(settings.admin_base_url), ca_file=settings.ca_file, token_file=settings.token_file
        ) as client:
            return await operation(client)

    try:
        # Check first so an existing output file cannot trigger a new remote run.
        if output and output.exists():
            raise ValueError("Output already exists")
        value = run(invoke())
        output_result(value, output)
        if value.get("state") in {"failed", "needs-attention"}:
            raise typer.Exit(1)
    except (OSError, ValueError, AdminClientError):
        typer.echo(
            "Population command failed; check inputs, output path, credentials and service.",
            err=True,
        )
        raise typer.Exit(1) from None


async def wait_run(client, value, wait, timeout):
    if not wait:
        return value
    end = monotonic() + timeout
    while value["state"] in {"pending", "running", "verifying"}:
        if monotonic() >= end:
            raise AdminClientError("Population is still pending after the wait deadline")
        await asyncio.sleep(1)
        value = await client.request("GET", f"/_mock/population/runs/{value['id']}")
    if value["state"] != "succeeded":
        raise AdminClientError("Population did not succeed")
    return value


@app.command("preview")
def preview(
    template: str = typer.Option(...),
    namespace: str = typer.Option(..., "--namespace"),
    tenant_id: UUID | None = typer.Option(None),
    create_tenant: Path | None = typer.Option(None),
    bundle: Path | None = typer.Option(None),
    parameters: Path | None = typer.Option(None),
    output: Path = typer.Option(...),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    if (tenant_id is None) == (create_tenant is None):
        raise typer.BadParameter("Select exactly one --tenant-id or --create-tenant")

    async def invoke(client):
        identifier = "builtin"
        if bundle:
            value = read_bundle(bundle)
            saved = await client.request(
                "POST",
                "/_mock/profiles",
                json={
                    "documents": {
                        k: v.model_dump(mode="json", exclude_unset=True)
                        for k, v in value.documents.items()
                    }
                },
            )
            identifier = saved["id"]
        return await client.request(
            "POST",
            "/_mock/population/previews",
            json={
                "bundle_id": identifier,
                "template": template,
                "dataset_namespace": namespace,
                "target": {"existing_tenant_id": str(tenant_id)}
                if tenant_id
                else {"create_tenant": read_object(create_tenant)},
                "parameters": read_object(parameters) if parameters else {},
            },
        )

    execute(config, invoke, output)


@app.command("apply")
def apply(
    preview_id: UUID = typer.Option(...),
    digest: str = typer.Option(...),
    wait: bool = typer.Option(False, "--wait"),
    timeout: int = typer.Option(600, min=1, max=86400),
    output: Path | None = typer.Option(None),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    async def invoke(client):
        value = await client.request(
            "POST", "/_mock/population/runs", json={"preview_id": str(preview_id), "digest": digest}
        )
        return await wait_run(client, value, wait, timeout)

    execute(config, invoke, output)


@app.command("status")
def status(
    run_id: UUID = typer.Option(...),
    wait: bool = typer.Option(False, "--wait"),
    timeout: int = typer.Option(600, min=1, max=86400),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    async def invoke(client):
        value = await client.request("GET", f"/_mock/population/runs/{run_id}")
        return await wait_run(client, value, wait, timeout)

    execute(config, invoke)


@app.command("cancel")
def cancel(run_id: UUID = typer.Option(...), config: Path = typer.Option(DEFAULT_CONFIG)):
    execute(
        config, lambda client: client.request("POST", f"/_mock/population/runs/{run_id}/cancel")
    )


@app.command("resume")
def resume(
    run_id: UUID = typer.Option(...),
    wait: bool = typer.Option(False, "--wait"),
    timeout: int = typer.Option(600, min=1, max=86400),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    async def invoke(client):
        value = await client.request("POST", f"/_mock/population/runs/{run_id}/resume")
        return await wait_run(client, value, wait, timeout)

    execute(config, invoke)


@app.command("manifest")
def manifest(
    run_id: UUID = typer.Option(...),
    output: Path = typer.Option(...),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    execute(
        config,
        lambda client: client.request("GET", f"/_mock/population/runs/{run_id}/manifest"),
        output,
    )


@app.command("ground-truth")
def ground_truth(
    run_id: UUID = typer.Option(...),
    output: Path = typer.Option(...),
    config: Path = typer.Option(DEFAULT_CONFIG),
):
    async def invoke(client):
        # Stream pages as JSON Lines so large labs never require a full in-memory export.
        after, count = None, 0
        with output.open("x", encoding="utf-8") as destination:
            while True:
                page = await client.request(
                    "GET",
                    f"/_mock/population/runs/{run_id}/ground-truth",
                    params={"after": after} if after else {},
                )
                destination.write(json.dumps(page) + "\n")
                count += len(page["items"])
                after = page["next_after"]
                if after is None:
                    break
        return {"output": str(output), "receipts": count}

    execute(config, invoke)

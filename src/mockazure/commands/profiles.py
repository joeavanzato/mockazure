"""Offline profile composition; does not connect to the emulator."""

import json
from datetime import UTC, datetime
from pathlib import Path

import typer

from mockazure.profiles.loader import (
    MAX_BUNDLE_BYTES,
    MAX_DOCUMENT_BYTES,
    load_bundle,
    parse_document,
)
from mockazure.profiles.resolver import resolve_template

app = typer.Typer(no_args_is_help=True)


def read_bundle(path: Path):
    files = sorted(path.iterdir()) if path.is_dir() else [path]
    documents, total = {}, 0
    for entry in files:
        if entry.suffix.lower() not in {".json", ".yaml", ".yml"}:
            continue
        if entry.is_symlink() or not entry.is_file():
            raise ValueError("Bundle contains an unsupported path")
        with entry.open("rb") as stream:
            data = stream.read(MAX_DOCUMENT_BYTES + 1)
        total += len(data)
        if total > MAX_BUNDLE_BYTES or len(documents) >= 128:
            raise ValueError("Bundle limit exceeded")
        documents[entry.name] = data
    if len(documents) == 1:
        value, _ = parse_document(next(iter(documents.values())))
        if set(value) == {"documents"} and isinstance(value["documents"], dict):
            return load_bundle(
                {key: json.dumps(doc).encode() for key, doc in value["documents"].items()}
            )
    return load_bundle(documents)


@app.command("validate")
def validate(bundle: Path = typer.Option(..., "--bundle")):
    try:
        parsed = read_bundle(bundle)
    except (OSError, ValueError):
        raise typer.BadParameter("Invalid or unavailable profile bundle") from None
    typer.echo(json.dumps({"valid": True, "profiles": sorted(parsed.documents)}))


@app.command("resolve")
def resolve(
    bundle: Path = typer.Option(..., "--bundle"),
    template: str = typer.Option(..., "--template"),
    output: Path = typer.Option(..., "--output"),
    parameters: Path | None = typer.Option(None, "--parameters"),
):
    try:
        overrides = {}
        if parameters:
            with parameters.open("rb") as source:
                overrides, _ = parse_document(source.read(MAX_DOCUMENT_BYTES + 1))
        result = resolve_template(read_bundle(bundle), template, overrides, datetime.now(UTC))
        with output.open("x", encoding="utf-8") as destination:
            destination.write(result.model_dump_json(indent=2) + "\n")
    except (OSError, ValueError) as error:
        message = "Profile resolution failed; check inputs and use a new output path"
        if isinstance(error, ValueError):
            message += ": " + str(error)
        raise typer.BadParameter(message) from None
    typer.echo(json.dumps({"output": str(output), "digest": result.digest}))


@app.command("list")
def list_profiles(
    config: Path = typer.Option(Path(".runtime/instance/native.json")),
    builtin: bool = typer.Option(False, "--builtin"),
):
    if builtin:
        from mockazure.profiles.builtin import builtin_bundle

        typer.echo(
            json.dumps(
                {
                    "profiles": [
                        {"name": d.name, "kind": d.kind}
                        for d in builtin_bundle().documents.values()
                    ]
                }
            )
        )
        return
    from mockazure.commands.populate import execute

    execute(config, lambda client: client.request("GET", "/_mock/profiles"))


@app.command("import")
def import_profiles(
    bundle: Path = typer.Option(...),
    config: Path = typer.Option(Path(".runtime/instance/native.json")),
):
    from mockazure.commands.populate import execute

    async def invoke(client):
        value = read_bundle(bundle)
        return await client.request(
            "POST",
            "/_mock/profiles",
            json={
                "documents": {
                    k: v.model_dump(mode="json", exclude_unset=True)
                    for k, v in value.documents.items()
                }
            },
        )

    execute(config, invoke)


@app.command("export")
def export_profiles(
    bundle_id: str = typer.Option("builtin"),
    output: Path = typer.Option(...),
    config: Path = typer.Option(Path(".runtime/instance/native.json")),
):
    from mockazure.commands.populate import execute

    async def invoke(client):
        value = await client.request("GET", "/_mock/profiles/" + bundle_id)
        return {"documents": value["documents"]}

    execute(config, invoke, output)

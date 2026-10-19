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

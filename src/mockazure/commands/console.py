"""Local administrator recovery and password setup; no unauthenticated HTTP setup."""

import getpass
from pathlib import Path

import typer
from sqlalchemy.exc import SQLAlchemyError

from mockazure.asyncio_support import run
from mockazure.commands.tenants import DEFAULT_CONFIG
from mockazure.config import load_settings
from mockazure.console.sessions import set_password
from mockazure.db.engine import close_database, create_database

app = typer.Typer(no_args_is_help=True)
password = typer.Typer(no_args_is_help=True)
app.add_typer(password, name="password")


@password.command("set")
def set_shared_password(
    config: Path = typer.Option(DEFAULT_CONFIG), password_file: Path | None = typer.Option(None)
):
    try:
        if password_file:
            with password_file.open("rb") as source:
                value = source.read(1027).decode("utf-8").removesuffix("\n").removesuffix("\r")
        else:
            value = getpass.getpass("New shared lab password: ")
            if value != getpass.getpass("Confirm password: "):
                raise ValueError("Passwords do not match")

        async def apply():
            database = create_database(load_settings(config))
            try:
                await set_password(database, value)
            finally:
                await close_database(database)

        run(apply())
    except (ValueError, OSError, SQLAlchemyError):
        typer.echo(
            "Password setup failed. Check configuration and use 12–1024 UTF-8 bytes.", err=True
        )
        raise typer.Exit(1) from None
    typer.echo("Shared lab password updated; previous browser sessions are revoked.")

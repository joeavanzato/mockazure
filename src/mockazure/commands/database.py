from pathlib import Path

import typer

from mockazure.asyncio_support import run
from mockazure.config import load_settings
from mockazure.db.engine import close_database, create_database
from mockazure.db.migrations import migrate, schema_is_current

app = typer.Typer()


@app.command()
def upgrade(config: Path = typer.Option(Path(".runtime/instance/native.json"))):
    try:
        migrate(load_settings(config).database_url.get_secret_value())
    except (ValueError, OSError):
        typer.echo("Migration failed; check configuration and PostgreSQL.", err=True)
        raise typer.Exit(2) from None
    typer.echo('{"schema":"current"}')


@app.command()
def status(config: Path = typer.Option(Path(".runtime/instance/native.json"))):
    async def check():
        database = create_database(load_settings(config))
        try:
            return await schema_is_current(database)
        finally:
            await close_database(database)

    try:
        current = run(check())
    except (ValueError, OSError):
        current = False
    typer.echo('{"schema":"current"}' if current else '{"schema":"not_ready"}')
    if not current:
        raise typer.Exit(1)

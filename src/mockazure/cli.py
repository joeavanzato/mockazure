import typer

from mockazure import __version__
from mockazure.commands import database, local

app = typer.Typer(no_args_is_help=True, invoke_without_command=True, pretty_exceptions_enable=False)


@app.callback()
def main(version: bool = typer.Option(False, "--version", is_eager=True)):
    """Run and administer a local MockAzure instance."""
    if version:
        typer.echo(__version__)
        raise typer.Exit()


app.command()(local.init)
app.add_typer(local.cert, name="cert")

app.add_typer(database.app, name="db")

import typer

from mockazure import __version__
from mockazure.commands import (
    clients,
    database,
    directory,
    evidence,
    identity,
    jobs,
    licensing,
    local,
    maintenance,
    managed_identity,
    profiles,
    tenants,
)

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

app.command()(local.serve)
app.add_typer(tenants.app, name="tenant")
app.command()(tenants.capabilities)
app.command()(tenants.health)

app.add_typer(jobs.app, name="job")
app.command()(jobs.worker)
tenants.app.command("export")(jobs.export)
tenants.app.command("import")(jobs.import_file)

app.add_typer(identity.app, name="identity")

app.command()(clients.client_config)

app.add_typer(directory.app, name="directory")

app.add_typer(licensing.app, name="licensing")

app.add_typer(managed_identity.app, name="managed-identity")

app.add_typer(evidence.app, name="evidence")

app.add_typer(maintenance.app, name="maintenance")

app.add_typer(profiles.app, name="profile")

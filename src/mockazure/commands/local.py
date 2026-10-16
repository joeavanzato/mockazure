import os
from pathlib import Path

import typer

from mockazure.bootstrap import initialize_instance
from mockazure.config import load_settings
from mockazure.tls import renew_leaf

cert = typer.Typer()


def init(
    data_dir: Path = typer.Option(Path(".runtime/instance")),
    advertised_host: str = "localhost",
    port: int = 8443,
):
    """Initialize local credentials. Set MOCKAZURE_DATABASE_URL first."""
    database_url = os.environ.get("MOCKAZURE_DATABASE_URL")
    if not database_url:
        raise typer.BadParameter("Set MOCKAZURE_DATABASE_URL to a PostgreSQL connection URL")
    try:
        files = initialize_instance(
            data_dir, database_url=database_url, advertised_host=advertised_host, port=port
        )
    except (ValueError, OSError):
        typer.echo(
            "Initialization failed; check configuration and existing instance files.", err=True
        )
        raise typer.Exit(2) from None
    typer.echo(f"Configuration: {files.config_file}\nCA: {files.ca_cert_file}")


@cert.command("renew")
def renew(config: Path = typer.Option(Path(".runtime/instance/native.json"))):
    """Explicitly renew the server leaf, preserving the instance CA."""
    try:
        settings = load_settings(config)
        files = renew_leaf(settings.data_dir, settings.allowed_hosts)
    except (ValueError, OSError):
        typer.echo("Renewal failed; check configuration and CA files.", err=True)
        raise typer.Exit(2) from None
    typer.echo(f"Renewed: {files.server_cert}")


def serve(config: Path = typer.Option(Path(".runtime/instance/native.json"))):
    """Serve the management API over instance-owned HTTPS."""
    import uvicorn

    from mockazure.admin.app import create_app
    from mockazure.asyncio_support import run
    from mockazure.tls import ensure_tls

    try:
        settings = load_settings(config)
        files = ensure_tls(settings.data_dir, settings.allowed_hosts)
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(settings),
                host=settings.listen_host,
                port=settings.listen_port,
                ssl_certfile=str(files.server_cert),
                ssl_keyfile=str(files.server_key),
                proxy_headers=False,
                access_log=False,
                log_level="warning",
            )
        )
        run(server.serve())
    except (ValueError, OSError):
        typer.echo("Server startup failed; check configuration and certificate files.", err=True)
        raise typer.Exit(2) from None

import json
import os
import re
import secrets
from dataclasses import dataclass
from ipaddress import IPv4Address
from pathlib import Path

from mockazure.config import Settings, load_settings, validate_host
from mockazure.endpoints import EndpointProfile
from mockazure.files import file_lock, write_private
from mockazure.tls import ensure_tls


@dataclass(frozen=True)
class InstanceFiles:
    config_file: Path
    admin_token_file: Path
    ca_cert_file: Path
    ca_key_file: Path
    server_cert_file: Path
    server_key_file: Path


def initialize_instance(
    data_dir: Path,
    *,
    database_url: str,
    advertised_host: str,
    port: int,
    bind_address: str = "127.0.0.1",
) -> InstanceFiles:
    data_dir = data_dir.resolve()
    advertised_host = validate_host(advertised_host)
    bind_address = str(IPv4Address(bind_address))
    settings = Settings(
        data_dir=data_dir,
        database_url=database_url,
        listen_port=port,
        listen_host=bind_address,
        admin_base_url=f"https://{advertised_host}:{port}",
        allowed_hosts=tuple(dict.fromkeys(("localhost", "127.0.0.1", advertised_host))),
    )
    files = InstanceFiles(
        *(
            data_dir / name
            for name in (
                "native.json",
                "admin.token",
                "ca.pem",
                "ca.key",
                "server.pem",
                "server.key",
            )
        )
    )
    with file_lock(data_dir, ".init.lock"):
        present = [path.exists() for path in vars(files).values()]
        if any(present) and not all(present):
            raise ValueError(
                "Partial instance initialization; restore missing files before continuing"
            )
        if all(present):
            existing = load_settings(files.config_file, {})
            if (
                existing.database_url != settings.database_url
                or str(existing.admin_base_url) != str(settings.admin_base_url)
                or existing.data_dir != settings.data_dir
                or existing.listen_host != settings.listen_host
            ):
                raise ValueError(
                    "Existing configuration differs; edit it explicitly instead of reinitializing"
                )
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", files.admin_token_file.read_text().strip()):
                raise ValueError("Invalid management token; restore from backup")
        else:
            ensure_tls(data_dir, settings.allowed_hosts)
            write_private(files.admin_token_file, (secrets.token_urlsafe(32) + "\n").encode())
            config = settings.model_dump(mode="json")
            config["data_dir"] = "."
            config["database_url"] = settings.database_url.get_secret_value()
            write_private(files.config_file, (json.dumps(config, indent=2) + "\n").encode())
        ensure_tls(data_dir, settings.allowed_hosts)
    return files


def initialize_compose(
    project_dir: Path,
    *,
    api_port: int = 8443,
    db_port: int = 55432,
    advertised_host: str = "localhost",
    bind_address: str = "127.0.0.1",
) -> InstanceFiles:
    project_dir = project_dir.resolve()
    bind_address = str(IPv4Address(bind_address))
    advertised_host = validate_host(advertised_host)
    if (
        not 1 <= api_port <= 65529
        or not 1 <= db_port <= 65535
        or db_port in range(api_port, api_port + 7)
    ):
        raise ValueError("Compose ports must be distinct valid TCP ports")
    runtime = project_dir / ".runtime"
    instance = runtime / "instance"
    password_file = runtime / "db.password"
    environment_file = runtime / "compose.env"
    container_file = instance / "container.json"
    native_file = instance / "native.json"
    with file_lock(runtime, ".compose.lock"):
        present = [
            p.exists() for p in (password_file, environment_file, container_file, native_file)
        ]
        if any(present) and not all(present):
            raise ValueError("Partial Compose initialization; restore missing files")
        base_environment = f"MOCKAZURE_API_PORT={api_port}\nMOCKAZURE_DB_PORT={db_port}\n"
        bind_environment = (
            f"MOCKAZURE_BIND_ADDRESS={bind_address}\n" if bind_address != "127.0.0.1" else ""
        )
        data_environment = "".join(
            f"MOCKAZURE_{name}_PORT={api_port + offset}\n"
            for offset, name in enumerate(
                ("STORAGE", "FILE", "QUEUE", "TABLE", "KEYVAULT", "LOGS"), 1
            )
        )
        legacy_storage = runtime / "postgres"
        cluster = legacy_storage / "18/docker"

        def valid_legacy_cluster():
            try:
                return (
                    (cluster / "PG_VERSION").read_text().strip() == "18"
                    and (cluster / "global/pg_control").is_file()
                    and (cluster / "global/pg_control").stat().st_size > 0
                )
            except OSError:
                return False

        storage = "postgres-data"
        upgrade_environment = False
        if all(present):
            existing_environment = environment_file.read_text(encoding="utf-8")
            if bind_environment:
                if not existing_environment.endswith(bind_environment):
                    raise ValueError("Existing Compose binding differs")
                existing_environment = existing_environment.removesuffix(bind_environment)
            if existing_environment.endswith(data_environment):
                existing_environment = existing_environment.removesuffix(data_environment)
            else:
                legacy_data = data_environment.rsplit("MOCKAZURE_LOGS_PORT=", 1)[0]
                if existing_environment.endswith(legacy_data):
                    existing_environment = existing_environment.removesuffix(legacy_data)
                upgrade_environment = True
            if existing_environment == base_environment:
                if not valid_legacy_cluster():
                    raise ValueError(
                        "Legacy Compose metadata has no complete PostgreSQL 18 cluster; "
                        "restore it or "
                        "archive the complete instance before initializing a fresh project"
                    )
                storage = "./.runtime/postgres"
                upgrade_environment = True
            elif existing_environment == (
                base_environment + "MOCKAZURE_POSTGRES_STORAGE=./.runtime/postgres\n"
            ):
                if not valid_legacy_cluster():
                    raise ValueError(
                        "Legacy PostgreSQL cluster is missing or incomplete; restore from backup"
                    )
                storage = "./.runtime/postgres"
            elif existing_environment != (
                base_environment + "MOCKAZURE_POSTGRES_STORAGE=postgres-data\n"
            ):
                raise ValueError("Compose configuration differs; inspect the existing settings")
            elif legacy_storage.exists():
                raise ValueError("Conflicting PostgreSQL storage: volume and legacy directory")
        elif legacy_storage.exists():
            raise ValueError("Legacy PostgreSQL storage has no complete instance configuration")
        if all(present):
            password = password_file.read_text().strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", password):
                raise ValueError("Invalid database credential file")
        else:
            password = secrets.token_urlsafe(32)
            write_private(password_file, (password + "\n").encode())
        files = initialize_instance(
            instance,
            database_url=f"postgresql://mockazure:{password}@127.0.0.1:{db_port}/mockazure",
            advertised_host=advertised_host,
            bind_address=bind_address,
            port=api_port,
        )
        settings = load_settings(files.config_file, {})
        container = settings.model_dump(mode="json")
        container.update(
            data_dir=".",
            listen_host="0.0.0.0",
            listen_port=8443,
            data_listen_ports=[8444, 8445, 8446, 8447, 8448, 8449],
            database_url=f"postgresql+psycopg://mockazure:{password}@db:5432/mockazure",
        )
        legacy_container = (json.dumps(container, indent=2) + "\n").encode()
        container["client_profiles"] = {
            **container["client_profiles"],
            "container": container["client_profiles"].get(
                "container", EndpointProfile().model_dump(mode="json")
            ),
        }
        container["population_endpoints"] = EndpointProfile(
            **{
                name: "https://api:8443"
                for name in ("login", "graph", "exchange", "arm", "activity", "browser_login")
            }
        ).model_dump(mode="json")
        container["allowed_hosts"] = list(dict.fromkeys([*container["allowed_hosts"], "api"]))
        upgrade_container = (
            container_file.exists() and container_file.read_bytes() == legacy_container
        )
        if container_file.exists() and not upgrade_container:
            # Normalize only newly defaulted fields; never overwrite a divergent existing setting.
            previous = json.loads(container_file.read_text(encoding="utf-8"))
            for name in ("console_enabled", "console_inspector_enabled", "console_public_origin"):
                previous.setdefault(name, container[name])
            if previous.get("client_profiles") == {}:
                previous["client_profiles"] = container["client_profiles"]
            if previous.get("population_endpoints") is None:
                previous["population_endpoints"] = container["population_endpoints"]
                previous["allowed_hosts"] = list(dict.fromkeys([*previous["allowed_hosts"], "api"]))
            if not previous.get("data_listen_ports"):
                previous["data_listen_ports"] = [8444, 8445, 8446, 8447, 8448, 8449]
            if previous.get("data_listen_ports") == [8444, 8445, 8446, 8447, 8448]:
                previous["data_listen_ports"].append(8449)
            for profile in previous.get("client_profiles", {}).values():
                defaults = EndpointProfile.model_validate(profile).model_dump(mode="json")
                for name in ("storage", "file", "queue", "table", "keyvault", "logs"):
                    profile.setdefault(name, defaults[name])
            upgrade_container = previous == container
        expected = {
            container_file: (json.dumps(container, indent=2) + "\n").encode(),
            environment_file: (
                base_environment
                + f"MOCKAZURE_POSTGRES_STORAGE={storage}\n"
                + data_environment
                + bind_environment
            ).encode(),
        }
        for path, content in expected.items():
            if (path == environment_file and upgrade_environment) or (
                path == container_file and upgrade_container
            ):
                temporary = path.with_name("." + path.name + ".upgrade")
                write_private(temporary, content)
                os.replace(temporary, path)
            elif path.exists():
                if path.read_bytes() != content:
                    raise ValueError("Compose configuration differs; inspect the existing settings")
            else:
                write_private(path, content)
    from cryptography import x509

    from mockazure.tls import renew_leaf

    certificate = x509.load_pem_x509_certificate(files.server_cert_file.read_bytes())
    sans = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    if "api" not in sans.get_values_for_type(x509.DNSName):
        renew_leaf(instance, tuple(container["allowed_hosts"]))
    return files

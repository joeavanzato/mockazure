import json
import os
import re
import secrets
from dataclasses import dataclass
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
    data_dir: Path, *, database_url: str, advertised_host: str, port: int
) -> InstanceFiles:
    data_dir = data_dir.resolve()
    advertised_host = validate_host(advertised_host)
    settings = Settings(
        data_dir=data_dir,
        database_url=database_url,
        listen_port=port,
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
    project_dir: Path, *, api_port: int = 8443, db_port: int = 55432
) -> InstanceFiles:
    project_dir = project_dir.resolve()
    if (
        not 1 <= api_port <= 65530
        or not 1 <= db_port <= 65535
        or db_port in range(api_port, api_port + 6)
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
        data_environment = "".join(
            f"MOCKAZURE_{name}_PORT={api_port + offset}\n"
            for offset, name in enumerate(("STORAGE", "FILE", "QUEUE", "TABLE", "KEYVAULT"), 1)
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
            if existing_environment.endswith(data_environment):
                existing_environment = existing_environment.removesuffix(data_environment)
            else:
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
            advertised_host="localhost",
            port=api_port,
        )
        settings = load_settings(files.config_file, {})
        container = settings.model_dump(mode="json")
        container.update(
            data_dir=".",
            listen_host="0.0.0.0",
            listen_port=8443,
            data_listen_ports=[],
            database_url=f"postgresql+psycopg://mockazure:{password}@db:5432/mockazure",
        )
        legacy_container = (json.dumps(container, indent=2) + "\n").encode()
        container["client_profiles"] = {
            **container["client_profiles"],
            "container": container["client_profiles"].get(
                "container", EndpointProfile().model_dump(mode="json")
            ),
        }
        upgrade_container = (
            container_file.exists() and container_file.read_bytes() == legacy_container
        )
        if container_file.exists() and not upgrade_container:
            # Normalize only newly defaulted fields; never overwrite a divergent existing setting.
            previous = json.loads(container_file.read_text(encoding="utf-8"))
            previous.setdefault("data_listen_ports", [])
            for profile in previous.get("client_profiles", {}).values():
                defaults = EndpointProfile.model_validate(profile).model_dump(mode="json")
                for name in ("storage", "file", "queue", "table", "keyvault"):
                    profile.setdefault(name, defaults[name])
            upgrade_container = previous == container
        expected = {
            container_file: (json.dumps(container, indent=2) + "\n").encode(),
            environment_file: (
                base_environment + f"MOCKAZURE_POSTGRES_STORAGE={storage}\n" + data_environment
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
    return files

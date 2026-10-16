import json
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from mockazure.config import Settings, load_settings, validate_host
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

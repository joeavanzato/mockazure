"""Explicit local configuration; errors must never echo database credentials."""

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mockazure.endpoints import EndpointProfile


def validate_host(value: str) -> str:
    value = value.lower()
    if value != "localhost" and not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value):
        raise ValueError("invalid host")
    if ".." in value or len(value) > 253:
        raise ValueError("invalid host")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid", hide_input_in_errors=True)
    data_dir: Path
    database_url: SecretStr
    listen_host: str = "127.0.0.1"
    listen_port: int = Field(default=8443, ge=1, le=65535)
    admin_base_url: AnyHttpUrl = "https://localhost:8443"
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1")
    service_endpoints: EndpointProfile | None = None
    pool_size: int = Field(default=5, ge=1, le=100)
    pool_max_overflow: int = Field(default=5, ge=0, le=100)

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        # Environment is selected and merged only by load_settings.
        return (init_settings,)

    @field_validator("database_url")
    @classmethod
    def database_is_postgres(cls, value):
        raw = value.get_secret_value()
        if raw.startswith("postgresql://"):
            raw = raw.replace("postgresql://", "postgresql+psycopg://", 1)
        parsed = urlsplit(raw)
        if (
            parsed.scheme != "postgresql+psycopg"
            or not parsed.hostname
            or not parsed.path.strip("/")
        ):
            raise ValueError("a PostgreSQL database URL is required")
        return SecretStr(raw)

    @field_validator("admin_base_url")
    @classmethod
    def admin_is_https(cls, value):
        if (
            value.scheme != "https"
            or value.username
            or value.password
            or value.query
            or value.fragment
        ):
            raise ValueError("admin base must be an HTTPS origin")
        if value.path not in ("", "/"):
            raise ValueError("admin base must be an HTTPS origin")
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def hosts_are_explicit(cls, value):
        if not value:
            raise ValueError("allowed_hosts cannot be empty")
        return tuple(dict.fromkeys(validate_host(host) for host in value))

    @property
    def endpoints(self) -> EndpointProfile:
        if self.service_endpoints is not None:
            return self.service_endpoints
        origin = str(self.admin_base_url).rstrip("/")
        return EndpointProfile(**{name: origin for name in EndpointProfile.model_fields})

    @property
    def token_file(self) -> Path:
        return self.data_dir / "admin.token"

    @property
    def ca_file(self) -> Path:
        return self.data_dir / "ca.pem"


def load_settings(config_file: Path, environ: Mapping[str, str] | None = None) -> Settings:
    try:
        config_file = config_file.resolve()
        values = json.loads(config_file.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError()
        for key, value in (os.environ if environ is None else environ).items():
            if key.startswith("MOCKAZURE_"):
                name = key.removeprefix("MOCKAZURE_").lower()
                if name in Settings.model_fields:
                    values[name] = (
                        json.loads(value)
                        if name in ("allowed_hosts", "service_endpoints")
                        else value
                    )
        values["data_dir"] = (config_file.parent / values["data_dir"]).resolve()
        return Settings(**values)
    except (ValueError, KeyError, TypeError, OSError):
        raise ValueError(
            "Invalid configuration; check the JSON file and MOCKAZURE_ settings"
        ) from None

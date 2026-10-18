from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, field_validator

from mockazure.authorization.catalog import permission
from mockazure.endpoints import local_origin


class IdentityInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    @field_validator("*")
    @classmethod
    def no_nul(cls, value):
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(raw, str) and "\x00" in raw:
            raise ValueError("Embedded NUL is unsupported")
        return value


class UserCreate(IdentityInput):
    upn: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=200)
    password: SecretStr
    enabled: bool = True

    @field_validator("upn")
    @classmethod
    def upn_valid(cls, value):
        value = value.strip().lower()
        if value.count("@") != 1 or any(c.isspace() for c in value):
            raise ValueError("Invalid UPN")
        name, domain = value.split("@")
        if not name or not domain or ".." in domain:
            raise ValueError("Invalid UPN")
        return value

    @field_validator("password")
    @classmethod
    def password_valid(cls, value):
        if not 12 <= len(value.get_secret_value().encode("utf-8")) <= 4096:
            raise ValueError("Invalid password length")
        return value


class RequiredPermission(IdentityInput):
    resource: Literal["graph", "exchange", "arm", "activity"]
    kind: Literal["application", "delegated"]
    permissions: list[str] = Field(min_length=1, max_length=64)

    @field_validator("permissions")
    @classmethod
    def permissions_valid(cls, value, info):
        resource, kind = info.data.get("resource"), info.data.get("kind")
        for name in value:
            permission(resource, kind, name)
        return sorted(set(value))


def validate_redirect_uri(value: str) -> str:
    parts = urlsplit(value)
    if parts.fragment or parts.username or parts.password or len(value) > 2048:
        raise ValueError("Invalid redirect URI")
    if parts.scheme == "http":
        if parts.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("HTTP redirects must be native loopback callbacks")
        local_origin("https://" + parts.netloc)
    else:
        local_origin(parts.scheme + "://" + parts.netloc)
    if "\\" in value:
        raise ValueError("Invalid redirect URI")
    return value


class ApplicationCreate(IdentityInput):
    display_name: str = Field(min_length=1, max_length=200)
    client_mode: Literal["public", "confidential"]
    redirect_uris: list[str] = Field(default_factory=list, max_length=32)
    required_permissions: list[RequiredPermission] = Field(default_factory=list, max_length=64)

    @field_validator("redirect_uris")
    @classmethod
    def redirects(cls, values):
        return list(dict.fromkeys(validate_redirect_uri(value) for value in values))


class CredentialCreate(IdentityInput):
    kind: Literal["secret", "certificate"]
    public_certificate: str | None = Field(default=None, max_length=16384)
    expires_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC) + timedelta(days=365)
    )

    @field_validator("expires_at")
    @classmethod
    def future_expiry(cls, value):
        if value <= datetime.now(UTC):
            raise ValueError("Credential must expire in the future")
        return value


class GrantCreate(RequiredPermission):
    client_id: UUID
    user_id: UUID | None = None


class EnabledUpdate(IdentityInput):
    enabled: bool


@dataclass(frozen=True)
class PrincipalContext:
    tenant_id: UUID
    client_id: UUID
    subject_id: UUID | None
    token_kind: Literal["application", "delegated"]
    audience: str
    permissions: frozenset[str]
    session_id: UUID | None = None

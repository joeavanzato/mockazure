from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator

from mockazure.identity.types import IdentityInput


def resource_id(value):
    if len(value) > 2048 or any(c in value for c in "?#\\\0") or not value.startswith("/"):
        raise ValueError("Invalid local resource ID")
    pieces = value.split("/")[1:]
    if (
        len(pieces) < 8
        or len(pieces) % 2
        or any(not p or p in {".", ".."} or any(c.isspace() for c in p) for p in pieces)
    ):
        raise ValueError("Invalid local resource ID")
    if (
        pieces[0].lower() != "subscriptions"
        or pieces[2].lower() != "resourcegroups"
        or pieces[4].lower() != "providers"
    ):
        raise ValueError("Invalid local resource ID")
    UUID(pieces[1])
    value.encode("utf-8")
    return value


class ManagedIdentityInput(IdentityInput):
    display_name: str = Field(min_length=1, max_length=200)
    kind: Literal["system", "user"]
    resource_id: str = Field(min_length=1, max_length=2048)

    @field_validator("resource_id")
    @classmethod
    def valid_resource(cls, value):
        return resource_id(value)

    @model_validator(mode="after")
    def user_resource(self):
        if self.kind == "user" and (
            len(self.resource_id.split("/")) != 9
            or "/providers/microsoft.managedidentity/userassignedidentities/"
            not in self.resource_id.lower()
        ):
            raise ValueError("User identity requires its managed identity resource ID")
        return self


class ManagedIdentityView(ManagedIdentityInput):
    id: UUID
    client_id: UUID
    principal_id: UUID
    enabled: bool


class HostInput(IdentityInput):
    name: str = Field(min_length=1, max_length=200)
    system_identity_id: UUID | None = None
    user_identity_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator("user_identity_ids")
    @classmethod
    def distinct(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate user identity")
        return sorted(value)


class HostUpdate(IdentityInput):
    enabled: bool | None = None
    system_identity_id: UUID | None = None
    user_identity_ids: list[UUID] | None = Field(default=None, max_length=100)

    @field_validator("user_identity_ids")
    @classmethod
    def distinct(cls, value):
        return HostInput.distinct(value) if value is not None else None


class IdentityUpdate(IdentityInput):
    enabled: bool


class ManagedTokenRequest(IdentityInput):
    header: SecretStr = Field(min_length=1, max_length=512)
    api_version: str
    resource: str = Field(min_length=1, max_length=2048)
    selector: Literal["client_id", "principal_id", "object_id", "mi_res_id"] | None = None
    selector_value: str | None = Field(default=None, max_length=2048)
    attempt_id: UUID
    ip_address: str | None = None

    @model_validator(mode="after")
    def paired_selector(self):
        if (self.selector is None) != (self.selector_value is None):
            raise ValueError("Selector and value must be supplied together")
        if self.selector_value == "":
            raise ValueError("Selector value cannot be empty")
        return self


class ManagedTokenResponse(IdentityInput):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    client_id: UUID
    resource: str
    expires_on: str

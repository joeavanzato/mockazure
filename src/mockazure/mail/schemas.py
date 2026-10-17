"""Strict management inputs for explicit mailbox state."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

EXCHANGE_COMMANDS = frozenset(
    {
        "Get-OrganizationConfig",
        "Get-Mailbox",
        "Set-Mailbox",
        "Get-EXOMailbox",
        "Get-EXOMailboxStatistics",
        "Get-EXOMailboxFolderStatistics",
        "Get-EXOCasMailbox",
        "Get-InboxRule",
        "New-InboxRule",
        "Set-InboxRule",
        "Remove-InboxRule",
        "Enable-InboxRule",
        "Disable-InboxRule",
        "Get-SweepRule",
        "New-SweepRule",
        "Set-SweepRule",
        "Remove-SweepRule",
        "Get-MessageTraceV2",
        "Get-MessageTraceDetailV2",
    }
)
CAS_FIELDS = frozenset(
    {
        "ActiveSyncEnabled",
        "EwsEnabled",
        "ImapEnabled",
        "MAPIEnabled",
        "OWAEnabled",
        "PopEnabled",
        "SmtpClientAuthenticationDisabled",
    }
)


class MailInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    @field_validator("*")
    @classmethod
    def text_values(cls, value):
        if isinstance(value, str):
            value.encode("utf-8")
            if "\0" in value:
                raise ValueError("NUL is unsupported")
        return value


def smtp_address(value):
    if not isinstance(value, str) or len(value) > 320:
        raise ValueError("Invalid SMTP address")
    value = value.strip().lower()
    if value.count("@") != 1 or any(c.isspace() or ord(c) < 33 for c in value):
        raise ValueError("Invalid SMTP address")
    local, domain = value.split("@")
    if not local or not domain or ".." in domain:
        raise ValueError("Invalid SMTP address")
    value.encode("utf-8")
    return value


class MailSettingsInput(MailInput):
    exchange_enabled: StrictBool = False
    audit_tier: Literal["none", "standard", "premium"] = "none"


class FolderInput(MailInput):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    path: str = Field(min_length=1, max_length=1024, pattern=r"^/")
    archive: StrictBool = False
    item_count: int = Field(default=0, strict=True, ge=0, le=2**63 - 1)
    size_bytes: int = Field(default=0, strict=True, ge=0, le=2**63 - 1)


class MailboxInput(MailInput):
    primary_smtp: str
    alias: str = Field(min_length=1, max_length=128)
    aliases: list[str] = Field(default_factory=list, max_length=128)
    mailbox_type: Literal["UserMailbox", "SharedMailbox", "RoomMailbox", "EquipmentMailbox"] = (
        "UserMailbox"
    )
    archive_enabled: StrictBool = False
    cas: dict[str, StrictBool] = Field(default_factory=dict)
    folders: list[FolderInput] = Field(default_factory=list, max_length=1000)

    @field_validator("primary_smtp")
    @classmethod
    def smtp(cls, value):
        return smtp_address(value)

    @field_validator("alias")
    @classmethod
    def normalized_alias(cls, value):
        if value != value.strip() or any(c.isspace() for c in value):
            raise ValueError("Invalid mailbox alias")
        return value.lower()

    @field_validator("aliases")
    @classmethod
    def addresses(cls, values):
        return sorted({smtp_address(value) for value in values})

    @field_validator("cas")
    @classmethod
    def cas_fields(cls, value):
        if value.keys() - CAS_FIELDS:
            raise ValueError("Unsupported CAS field")
        return value

    @model_validator(mode="after")
    def folder_state(self):
        if len({folder.id for folder in self.folders}) != len(self.folders):
            raise ValueError("Duplicate folder ID")
        if not self.archive_enabled and any(folder.archive for folder in self.folders):
            raise ValueError("Archive folders require an enabled archive")
        return self


class CommandGrantInput(MailInput):
    user_id: UUID | None = None
    client_id: UUID | None = None
    mailbox_user_id: UUID | None = None
    commands: list[str] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def principal_and_commands(self):
        if (self.user_id is None) == (self.client_id is None):
            raise ValueError("Exactly one principal is required")
        if set(self.commands) - EXCHANGE_COMMANDS:
            raise ValueError("Unsupported Exchange command grant")
        return self


class RuleSeedInput(MailInput):
    rule: dict
    hidden: StrictBool = False
    read_only: StrictBool = False

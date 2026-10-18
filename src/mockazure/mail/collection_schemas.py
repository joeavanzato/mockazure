"""Bounded Exchange collection import schemas, not an operational transport engine."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictBool, field_validator, model_validator

from mockazure.mail.schemas import MailInput


class ExchangeInput(MailInput):
    @field_validator("*")
    @classmethod
    def nested_text(cls, value):
        def check(item):
            if isinstance(item, str):
                item.encode("utf-8")
                if "\0" in item:
                    raise ValueError("NUL is unsupported")
            elif isinstance(item, list):
                for child in item:
                    check(child)

        check(value)
        return value


class OrganizationConfiguration(ExchangeInput):
    AuditDisabled: StrictBool | None = None
    OAuth2ClientProfileEnabled: StrictBool | None = None
    MailTipsAllTipsEnabled: StrictBool | None = None
    PublicFoldersEnabled: Literal["None", "Local", "Remote"] | None = None
    DefaultPublicFolderIssueWarningQuota: str | None = Field(default=None, max_length=128)


class TransportConfiguration(ExchangeInput):
    SmtpClientAuthenticationDisabled: StrictBool | None = None
    ExternalPostmasterAddress: str | None = Field(default=None, max_length=320)
    MaxReceiveSize: str | None = Field(default=None, max_length=128)
    MaxSendSize: str | None = Field(default=None, max_length=128)
    MaxRecipientEnvelopeLimit: str | None = Field(default=None, max_length=128)
    InternalSMTPServers: list[str] = Field(default_factory=list, max_length=256)


class AcceptedDomain(ExchangeInput):
    Guid: UUID
    Name: str = Field(min_length=1, max_length=256)
    DomainName: str = Field(min_length=1, max_length=253)
    DomainType: Literal["Authoritative", "InternalRelay"]
    Default: StrictBool = False


class TransportRule(ExchangeInput):
    Guid: UUID
    Name: str = Field(min_length=1, max_length=256)
    Priority: int = Field(strict=True, ge=0, le=10000)
    State: Literal["Enabled", "Disabled"]
    Mode: Literal["Enforce", "Audit", "AuditAndNotify"]
    Comments: str | None = Field(default=None, max_length=4096)
    Description: str | None = Field(default=None, max_length=16384)
    CreatedBy: str | None = Field(default=None, max_length=320)
    WhenChanged: AwareDatetime | None = None
    WhenCreated: AwareDatetime | None = None
    From: list[str] = Field(default_factory=list, max_length=256)
    SentTo: list[str] = Field(default_factory=list, max_length=256)
    RedirectMessageTo: list[str] = Field(default_factory=list, max_length=256)
    BlindCopyTo: list[str] = Field(default_factory=list, max_length=256)
    DeleteMessage: StrictBool | None = None
    StopRuleProcessing: StrictBool | None = None


class MailboxPermission(ExchangeInput):
    id: UUID
    mailbox_user_id: UUID
    User: str = Field(min_length=1, max_length=320)
    AccessRights: list[
        Literal[
            "ChangeOwner",
            "ChangePermission",
            "DeleteItem",
            "ExternalAccount",
            "FullAccess",
            "ReadPermission",
        ]
    ] = Field(min_length=1, max_length=6)
    IsInherited: StrictBool
    Deny: StrictBool


class RecipientPermission(ExchangeInput):
    id: UUID
    mailbox_user_id: UUID
    Trustee: str = Field(min_length=1, max_length=320)
    AccessRights: list[Literal["SendAs"]] = Field(min_length=1, max_length=1)
    AccessControlType: Literal["Allow", "Deny"]
    IsInherited: StrictBool
    InheritanceType: Literal["None", "All", "Descendents", "SelfAndChildren", "Children"]


class ExchangeCollectionState(ExchangeInput):
    organization: OrganizationConfiguration = Field(default_factory=OrganizationConfiguration)
    transport: TransportConfiguration = Field(default_factory=TransportConfiguration)
    accepted_domains: list[AcceptedDomain] = Field(default_factory=list, max_length=1000)
    transport_rules: list[TransportRule] = Field(default_factory=list, max_length=1000)
    mailbox_permissions: list[MailboxPermission] = Field(default_factory=list, max_length=10000)
    recipient_permissions: list[RecipientPermission] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def unique_keys(self):
        for rows, key in (
            (self.accepted_domains, "Guid"),
            (self.transport_rules, "Guid"),
            (self.mailbox_permissions, "id"),
            (self.recipient_permissions, "id"),
        ):
            if len({getattr(row, key) for row in rows}) != len(rows):
                raise ValueError("Duplicate record identity")
        if len({row.Name.casefold() for row in self.accepted_domains}) != len(
            self.accepted_domains
        ):
            raise ValueError("Duplicate accepted domain name")
        if len({row.DomainName.casefold() for row in self.accepted_domains}) != len(
            self.accepted_domains
        ):
            raise ValueError("Duplicate domain")
        if sum(row.Default for row in self.accepted_domains) > 1:
            raise ValueError("Multiple default domains")
        if len({row.Priority for row in self.transport_rules}) != len(self.transport_rules):
            raise ValueError("Duplicate rule priority")
        return self

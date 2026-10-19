"""Bounded typed cloud configuration; unknown fields never become operational state."""

import ipaddress
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    create_model,
    field_validator,
    model_validator,
)

Text = Annotated[str, Field(strict=True, max_length=1024)]
Name = Annotated[str, Field(strict=True, min_length=1, max_length=64)]
Strings = Annotated[list[Text], Field(max_length=1000)]


def domain(value, *, wildcard=True):
    value = value.lower()
    if wildcard and value == "*":
        return value
    candidate = value[2:] if wildcard and value.startswith("*.") else value
    if (
        len(candidate) > 253
        or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*", candidate
        )
        or any(len(v) > 63 for v in candidate.split("."))
    ):
        raise ValueError("Invalid domain")
    return value


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    Name: Name

    @field_validator("*")
    @classmethod
    def clean(cls, value):
        values = value if isinstance(value, list) else [value]
        if any(isinstance(v, str) and ("\0" in v or not v.strip()) for v in values):
            raise ValueError("Invalid configuration text")
        return value


class RemoteDomain(Configuration):
    DomainName: Text
    AutoForwardEnabled: StrictBool = True
    AutoReplyEnabled: StrictBool = True
    AllowedOOFType: Literal["External", "ExternalLegacy", "InternalLegacy", "None"] = "External"
    DeliveryReportEnabled: StrictBool = True
    NDREnabled: StrictBool = True
    IsInternal: StrictBool = False
    MeetingForwardNotificationEnabled: StrictBool = True
    TNEFEnabled: StrictBool | None = None
    TargetDeliveryDomain: StrictBool = False

    @model_validator(mode="after")
    def internal_oof(self):
        if self.AllowedOOFType == "InternalLegacy":
            self.IsInternal = True
        return self

    @field_validator("DomainName")
    @classmethod
    def valid_domain(cls, v):
        return domain(v)


class Connector(Configuration):
    Enabled: StrictBool = True
    ConnectorType: Literal["OnPremises", "Partner"] = "OnPremises"
    ConnectorSource: Literal["Default", "HybridWizard", "Migrated"] = "Default"
    Comment: Text | None = None
    CloudServicesMailEnabled: StrictBool = False


class InboundConnector(Connector):
    SenderDomains: Strings
    SenderIPAddresses: Strings = Field(default_factory=list)
    RequireTls: StrictBool = True
    RestrictDomainsToCertificate: StrictBool = False
    RestrictDomainsToIPAddresses: StrictBool = False
    TlsSenderCertificateName: Text | None = None
    TreatMessagesAsInternal: StrictBool = False

    @field_validator("SenderDomains")
    @classmethod
    def valid_domains(cls, v):
        if not v:
            raise ValueError("SenderDomains required")
        return [domain(x) for x in v]

    @field_validator("SenderIPAddresses")
    @classmethod
    def valid_ips(cls, v):
        for x in v:
            network = ipaddress.ip_network(x, strict=False)
            if network.version != 4 or network.prefixlen < 24:
                raise ValueError("Only IPv4 with /24 through /32 is supported")
        return v

    @model_validator(mode="after")
    def constraints(self):
        if self.TreatMessagesAsInternal and (
            self.ConnectorType != "OnPremises" or self.CloudServicesMailEnabled
        ):
            raise ValueError(
                "Internal messages require OnPremises without CloudServicesMailEnabled"
            )
        if self.TlsSenderCertificateName:
            domain(self.TlsSenderCertificateName)
        if self.RestrictDomainsToCertificate and not self.TlsSenderCertificateName:
            raise ValueError("Certificate name required")
        if self.RestrictDomainsToIPAddresses and not self.SenderIPAddresses:
            raise ValueError("Sender IPs required")
        return self


class OutboundConnector(Connector):
    RecipientDomains: Strings = Field(default_factory=list)
    SmartHosts: Strings = Field(default_factory=list)
    UseMXRecord: StrictBool = True
    AllAcceptedDomains: StrictBool = False
    IsTransportRuleScoped: StrictBool = False
    TestMode: StrictBool = False
    RouteAllMessagesViaOnPremises: StrictBool = False
    SenderRewritingEnabled: StrictBool = False
    TlsDomain: Text | None = None
    TlsSettings: Literal["EncryptionOnly", "CertificateValidation", "DomainValidation"] | None = (
        None
    )

    @field_validator("RecipientDomains")
    @classmethod
    def valid_domains(cls, v):
        return [domain(x) for x in v]

    @field_validator("SmartHosts")
    @classmethod
    def valid_hosts(cls, v):
        for x in v:
            try:
                ipaddress.ip_address(x)
            except ValueError:
                domain(x, wildcard=False)
        return v

    @field_validator("TlsDomain")
    @classmethod
    def valid_tls(cls, v):
        return domain(v) if v else v

    @model_validator(mode="after")
    def constraints(self):
        if self.UseMXRecord == bool(self.SmartHosts):
            raise ValueError("Choose MX routing or smart hosts")
        if self.TlsSettings == "DomainValidation" and not self.TlsDomain:
            raise ValueError("TLS domain required")
        if self.ConnectorType != "OnPremises" and (
            self.RouteAllMessagesViaOnPremises or self.SenderRewritingEnabled
        ):
            raise ValueError("OnPremises connector required")
        return self


class OrganizationRelationship(Configuration):
    DomainNames: Strings
    Enabled: StrictBool = True
    FreeBusyAccessEnabled: StrictBool = False
    FreeBusyAccessLevel: Literal["None", "AvailabilityOnly", "LimitedDetails"] = "None"
    MailTipsAccessEnabled: StrictBool = False
    MailTipsAccessLevel: Literal["None", "All", "Limited"] = "None"
    DeliveryReportEnabled: StrictBool = False
    PhotosEnabled: StrictBool = False

    @field_validator("DomainNames")
    @classmethod
    def valid_domains(cls, v):
        if not v:
            raise ValueError("DomainNames required")
        return [domain(x, wildcard=False) for x in v]


class SharingPolicy(Configuration):
    Domains: Strings
    Default: StrictBool = False
    Enabled: StrictBool = True

    @field_validator("Domains")
    @classmethod
    def valid_domains(cls, values):
        if not values:
            raise ValueError("Sharing domains required")
        result = []
        for v in values:
            name, sep, actions = v.partition(":")
            name = name.strip()
            if name.lower() != "anonymous":
                name = domain(name)
            if name.startswith("*."):
                raise ValueError("Sharing domains cannot contain subdomain wildcards")
            actions = [a.strip() for a in actions.split(",")]
            if not sep or set(actions) - {
                "CalendarSharingFreeBusySimple",
                "CalendarSharingFreeBusyDetail",
                "CalendarSharingFreeBusyReviewer",
                "ContactsSharing",
            }:
                raise ValueError("Invalid sharing action")
            result.append(name + ":" + ", ".join(actions))
        return result


BASIC_PROTOCOLS = (
    "ActiveSync",
    "Autodiscover",
    "Imap",
    "Mapi",
    "OfflineAddressBook",
    "OutlookService",
    "Pop",
    "Powershell",
    "ReportingWebServices",
    "Rpc",
    "Smtp",
    "WebServices",
)
AuthenticationPolicy = create_model(
    "AuthenticationPolicy",
    __base__=Configuration,
    **{"AllowBasicAuth" + p: (StrictBool, False) for p in BASIC_PROTOCOLS},
)
MODELS = {
    m.__name__: m
    for m in (
        RemoteDomain,
        InboundConnector,
        OutboundConnector,
        AuthenticationPolicy,
        OrganizationRelationship,
        SharingPolicy,
    )
}


class SecurityRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    kind: Literal[
        "RemoteDomain",
        "InboundConnector",
        "OutboundConnector",
        "AuthenticationPolicy",
        "OrganizationRelationship",
        "SharingPolicy",
    ]
    properties: dict

    @model_validator(mode="after")
    def typed(self):
        self.properties = MODELS[self.kind].model_validate(self.properties).model_dump(mode="json")
        return self


class SecurityState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    records: list[SecurityRecord] = Field(default_factory=list, max_length=10000)
    unified_audit_log_ingestion_enabled: StrictBool = True

    @model_validator(mode="after")
    def unique(self):
        keys = [(r.kind, r.properties["Name"].casefold()) for r in self.records]
        if len(keys) != len(set(keys)) or len({r.id for r in self.records}) != len(keys):
            raise ValueError("Duplicate configuration")
        if sum(r.kind == "SharingPolicy" and r.properties["Default"] for r in self.records) > 1:
            raise ValueError("Multiple default policies")
        domains = [r.properties["DomainName"] for r in self.records if r.kind == "RemoteDomain"]
        if len(domains) != len(set(domains)):
            raise ValueError("Duplicate remote domain")
        return self

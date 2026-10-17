"""Explicit immutable imported message trace facts."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, IPvAnyAddress, field_validator

from mockazure.mail.schemas import MailInput, smtp_address


class TraceDetailInput(MailInput):
    date: AwareDatetime
    event: str = Field(min_length=1, max_length=128)
    action: str = Field(default="", max_length=256)
    detail: str = Field(default="", max_length=16384)
    data: str = Field(default="", max_length=65536)


class TraceInput(MailInput):
    message_trace_id: UUID
    received: AwareDatetime
    sender_address: str
    recipient_address: str
    mailbox_user_id: UUID | None = None
    message_id: str = Field(min_length=1, max_length=1024)
    subject: str = Field(default="", max_length=2048)
    status: Literal[
        "Delivered",
        "Expanded",
        "Failed",
        "FilteredAsSpam",
        "GettingStatus",
        "Pending",
        "Quarantined",
    ]
    size: int = Field(default=0, strict=True, ge=0, le=2**63 - 1)
    from_ip: IPvAnyAddress | None = None
    to_ip: IPvAnyAddress | None = None
    details: list[TraceDetailInput] = Field(default_factory=list, max_length=1000)

    @field_validator("sender_address", "recipient_address")
    @classmethod
    def address(cls, value):
        return smtp_address(value)


class TraceImport(MailInput):
    source: str = Field(min_length=1, max_length=512)
    records: list[TraceInput] = Field(min_length=1, max_length=1000)

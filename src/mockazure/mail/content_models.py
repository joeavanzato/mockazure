"""Bounded administrative mail imports validated against pinned Graph v1.0 types."""

from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, field_validator, model_validator

from mockazure.mail.schemas import MailInput
from mockazure.providers.arm.activity import instant
from mockazure.providers.graph.schema_reference import validate_payload

WELL_KNOWN = frozenset(
    {
        "archive",
        "clutter",
        "conflicts",
        "conversationhistory",
        "deleteditems",
        "drafts",
        "inbox",
        "junkemail",
        "localfailures",
        "outbox",
        "recoverableitemsdeletions",
        "scheduled",
        "searchfolders",
        "sentitems",
        "serverfailures",
        "syncissues",
    }
)


class FolderImport(MailInput):
    id: str = Field(min_length=1, max_length=128)
    displayName: str = Field(min_length=1, max_length=256)
    parentFolderId: str | None = Field(default=None, min_length=1, max_length=128)
    isHidden: StrictBool = False
    well_known_name: str | None = None

    @field_validator("well_known_name")
    @classmethod
    def known(cls, value):
        if value is not None and value.casefold() not in WELL_KNOWN:
            raise ValueError("Unsupported well-known folder")
        return value.casefold() if value else None


class MessageImport(MailInput):
    id: str = Field(min_length=1, max_length=512)
    immutable_id: UUID | None = None
    folder_id: str = Field(min_length=1, max_length=128)
    mime_content_id: UUID | None = None
    size_bytes: int = Field(default=0, ge=0, le=2**63 - 1, strict=True)
    payload: dict = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def shape(cls, value):
        if value.keys() & {"id", "parentFolderId", "changeKey", "hasAttachments"}:
            raise ValueError("Message identity and derived properties are managed separately")
        validate_payload("v1.0", "message", value, partial=True)
        if value.get("receivedDateTime") is not None:
            instant(value["receivedDateTime"])
        return value


class AttachmentImport(MailInput):
    message_id: str = Field(min_length=1, max_length=512)
    id: str = Field(min_length=1, max_length=128)
    kind: Literal["fileAttachment", "itemAttachment", "referenceAttachment"]
    content_id: UUID | None = None
    payload: dict = Field(default_factory=dict)
    item: dict | None = None

    @model_validator(mode="after")
    def shape(self):
        if self.payload.keys() & {"id", "contentBytes", "@odata.type"}:
            raise ValueError("Attachment identity and bytes are managed separately")
        validate_payload("v1.0", self.kind, self.payload, partial=True)
        media_type = self.payload.get("contentType")
        if media_type is not None and (
            len(media_type) > 255 or any(not 32 <= ord(c) <= 126 for c in media_type)
        ):
            raise ValueError("Attachment contentType must be a bounded printable HTTP header")
        if self.kind == "referenceAttachment" and (self.content_id or self.item):
            raise ValueError("Reference attachments have no local raw content")
        if self.kind == "fileAttachment" and (self.content_id is None or self.item):
            raise ValueError("File attachment requires local bytes")
        if self.item is not None:
            if (
                self.kind != "itemAttachment"
                or self.item.get("@odata.type") != "#microsoft.graph.message"
            ):
                raise ValueError("Only typed message item attachments are supported")
            validate_payload("v1.0", "message", self.item, partial=True)
        return self


class MailContentImport(MailInput):
    mailbox_user_id: UUID
    mode: Literal["content-backed"]
    expected_revision: int = Field(ge=0, strict=True)
    folders: list[FolderImport] = Field(default_factory=list, max_length=1000)
    messages: list[MessageImport] = Field(default_factory=list, max_length=1000)
    attachments: list[AttachmentImport] = Field(default_factory=list, max_length=1000)
    tombstones: list[str] = Field(default_factory=list, max_length=1000)
    mailbox_settings: dict | None = None

    @field_validator("mailbox_settings")
    @classmethod
    def settings_shape(cls, value):
        if value is not None:
            validate_payload("v1.0", "mailboxSettings", value, partial=True)
        return value

    @model_validator(mode="after")
    def bounded(self):
        if (
            sum(len(v) for v in (self.folders, self.messages, self.attachments, self.tombstones))
            > 1000
        ):
            raise ValueError("Mail import exceeds 1000 records")
        for values in (
            [v.id for v in self.folders],
            [v.id for v in self.messages],
            self.tombstones,
            [(v.message_id, v.id) for v in self.attachments],
        ):
            if len(set(values)) != len(values):
                raise ValueError("Duplicate import identity")
        immutable_ids = [v.immutable_id for v in self.messages if v.immutable_id]
        if len(set(immutable_ids)) != len(immutable_ids):
            raise ValueError("Duplicate immutable message identity")
        if set(self.tombstones) & {v.message_id for v in self.attachments}:
            raise ValueError("Cannot attach evidence to a deleted message")
        if set(self.tombstones) & {v.id for v in self.messages}:
            raise ValueError("Message cannot be imported and deleted in the same batch")
        if any(not 1 <= len(v) <= 512 for v in self.tombstones):
            raise ValueError("Invalid tombstone identity")
        return self


class ImportResult(MailInput):
    revision: int
    count: int

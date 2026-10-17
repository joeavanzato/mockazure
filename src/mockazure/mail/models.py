"""Tenant-scoped canonical mailbox state and explicit Exchange command scopes."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class MailSettings(Base):
    __tablename__ = "mail_settings"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    exchange_enabled: Mapped[bool] = mapped_column(default=False)
    audit_tier: Mapped[str] = mapped_column(String(10), default="none")
    __table_args__ = (CheckConstraint("audit_tier IN ('none','standard','premium')"),)


class Mailbox(Base):
    __tablename__ = "mail_mailboxes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "forward_user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
        UniqueConstraint("tenant_id", "exchange_guid"),
        UniqueConstraint("tenant_id", "primary_smtp"),
        UniqueConstraint("tenant_id", "archive_guid"),
        CheckConstraint(
            "mailbox_type IN ('UserMailbox','SharedMailbox','RoomMailbox','EquipmentMailbox')"
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    exchange_guid: Mapped[UUID] = mapped_column(default=uuid4)
    primary_smtp: Mapped[str] = mapped_column(String(320))
    alias: Mapped[str] = mapped_column(String(128))
    aliases: Mapped[list] = mapped_column(JSONB, default=list)
    mailbox_type: Mapped[str] = mapped_column(String(30), default="UserMailbox")
    archive_guid: Mapped[UUID | None]
    cas: Mapped[dict] = mapped_column(JSONB, default=dict)
    forward_user_id: Mapped[UUID | None]
    forward_smtp: Mapped[str | None] = mapped_column(String(325))
    deliver_and_forward: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MailboxFolder(Base):
    __tablename__ = "mail_folders"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["mail_mailboxes.tenant_id", "mail_mailboxes.user_id"]
        ),
        CheckConstraint("item_count >= 0 AND size_bytes >= 0"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    path: Mapped[str] = mapped_column(String(1024))
    archive: Mapped[bool] = mapped_column(default=False)
    item_count: Mapped[int] = mapped_column(BigInteger, default=0)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)


class ExchangeCommandGrant(Base):
    __tablename__ = "mail_command_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["identity_service_principals.tenant_id", "identity_service_principals.client_id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "mailbox_user_id"], ["mail_mailboxes.tenant_id", "mail_mailboxes.user_id"]
        ),
        CheckConstraint("(user_id IS NULL) <> (client_id IS NULL)"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[UUID | None]
    client_id: Mapped[UUID | None]
    mailbox_user_id: Mapped[UUID | None]
    commands: Mapped[list] = mapped_column(JSONB)

"""Canonical mail content and immutable historical versions."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class MailboxContentState(Base):
    __tablename__ = "mail_content_state"
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    generation: Mapped[UUID] = mapped_column(default=uuid4)
    settings: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["mail_mailboxes.tenant_id", "mail_mailboxes.user_id"],
            ondelete="CASCADE",
        ),
    )


class MailContentFolder(Base):
    __tablename__ = "mail_content_folders"
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(String(128))
    is_hidden: Mapped[bool] = mapped_column(default=False)
    well_known_name: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id", "id"],
            ["mail_folders.tenant_id", "mail_folders.user_id", "mail_folders.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "user_id", "well_known_name"),
    )


class MailMessage(Base):
    __tablename__ = "mail_messages"
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    immutable_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    id: Mapped[str] = mapped_column(String(512))
    folder_id: Mapped[str] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSONB)
    mime_content_id: Mapped[UUID | None]
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    attachments: Mapped[list] = mapped_column(JSONB, default=list)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id", "folder_id"],
            ["mail_folders.tenant_id", "mail_folders.user_id", "mail_folders.id"],
        ),
        UniqueConstraint("tenant_id", "user_id", "id"),
    )


class MailMessageVersion(Base):
    __tablename__ = "mail_message_versions"
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    immutable_id: Mapped[UUID] = mapped_column(primary_key=True)
    valid_from: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    valid_to: Mapped[int | None] = mapped_column(BigInteger)
    id: Mapped[str] = mapped_column(String(512))
    folder_id: Mapped[str] = mapped_column(String(128))
    deleted: Mapped[bool] = mapped_column(default=False)
    payload: Mapped[dict] = mapped_column(JSONB)
    mime_content_id: Mapped[UUID | None]
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    attachments: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["mail_mailboxes.tenant_id", "mail_mailboxes.user_id"],
            ondelete="CASCADE",
        ),
    )


class MailContentCursor(Base):
    __tablename__ = "mail_content_cursors"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[UUID]
    user_id: Mapped[UUID]
    state: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["mail_mailboxes.tenant_id", "mail_mailboxes.user_id"],
            ondelete="CASCADE",
        ),
    )

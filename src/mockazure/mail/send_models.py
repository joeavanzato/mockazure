"""Pending local sends and capability-bound attachment uploads."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKeyConstraint, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class MailDelivery(Base):
    __tablename__ = "mail_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["mail_content_state.tenant_id", "mail_content_state.user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["id"], ["jobs.id"], ondelete="CASCADE"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    user_id: Mapped[UUID]
    message_id: Mapped[UUID]
    generation: Mapped[UUID]
    evidence_generation: Mapped[UUID]
    cloud_generation: Mapped[int] = mapped_column(BigInteger)
    actor: Mapped[dict] = mapped_column(JSONB)
    represented_id: Mapped[UUID]
    mode: Mapped[str] = mapped_column(String(20))
    save_sent: Mapped[bool]
    outcome: Mapped[dict | None] = mapped_column(JSONB)


class MailAttachmentUpload(Base):
    __tablename__ = "mail_attachment_uploads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["mail_content_state.tenant_id", "mail_content_state.user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id", "message_id"],
            ["mail_messages.tenant_id", "mail_messages.user_id", "mail_messages.immutable_id"],
            ondelete="CASCADE",
        ),
    )
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[UUID]
    user_id: Mapped[UUID]
    message_id: Mapped[UUID]
    generation: Mapped[UUID]
    evidence_generation: Mapped[UUID]
    cloud_generation: Mapped[int] = mapped_column(BigInteger)
    content_id: Mapped[UUID]
    expected: Mapped[int] = mapped_column(BigInteger)
    received: Mapped[int] = mapped_column(BigInteger, default=0)
    item: Mapped[dict] = mapped_column(JSONB)
    actor: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

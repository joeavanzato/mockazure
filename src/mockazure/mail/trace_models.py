"""Trace rows are immutable imports, separate from operational delivery."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class MailTrace(Base):
    __tablename__ = "mail_traces"
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    message_trace_id: Mapped[UUID] = mapped_column(primary_key=True)
    recipient_address: Mapped[str] = mapped_column(String(320), primary_key=True)
    received: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sender_address: Mapped[str] = mapped_column(String(320))
    mailbox_user_id: Mapped[UUID | None]
    message_id: Mapped[str] = mapped_column(String(1024))
    subject: Mapped[str] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(32))
    size: Mapped[int] = mapped_column(BigInteger)
    from_ip: Mapped[str | None] = mapped_column(String(45))
    to_ip: Mapped[str | None] = mapped_column(String(45))
    provenance: Mapped[str] = mapped_column(String(512))
    payload: Mapped[dict] = mapped_column(JSONB)


class MailTraceDetail(Base):
    __tablename__ = "mail_trace_details"
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    message_trace_id: Mapped[UUID] = mapped_column(primary_key=True)
    recipient_address: Mapped[str] = mapped_column(String(320), primary_key=True)
    sequence: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(256))
    detail: Mapped[str]
    data: Mapped[str]

"""Durable Queue messages; receipt authority is stored only as a digest."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKeyConstraint, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class QueueMessage(Base):
    __tablename__ = "storage_queue_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "account_id", "service", "queue"],
            [
                "storage_namespaces.tenant_id",
                "storage_namespaces.account_id",
                "storage_namespaces.service",
                "storage_namespaces.name",
            ],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[str] = mapped_column(String(2048))
    service: Mapped[str] = mapped_column(String(8), default="queue")
    queue: Mapped[str] = mapped_column(String(63))
    body: Mapped[str] = mapped_column(Text)
    inserted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    visible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dequeue_count: Mapped[int] = mapped_column(BigInteger, default=0)
    receipt_hash: Mapped[str | None] = mapped_column(String(64))


class QueueCursor(Base):
    __tablename__ = "storage_queue_cursors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["storage_accounts.tenant_id", "storage_accounts.scope_key"],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[str] = mapped_column(String(2048))
    generation: Mapped[int] = mapped_column(BigInteger)
    query: Mapped[dict] = mapped_column(JSONB)
    last_key: Mapped[str] = mapped_column(String(63))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

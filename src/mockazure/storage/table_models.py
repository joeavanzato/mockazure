"""Tenant-scoped Table state, typed values, and opaque generation-bound continuations."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class Table(Base):
    __tablename__ = "storage_tables"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["storage_accounts.tenant_id", "storage_accounts.scope_key"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "account_id", "name_key"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[str] = mapped_column(String(2048))
    name: Mapped[str] = mapped_column(String(63))
    name_key: Mapped[str] = mapped_column(String(63))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TableEntity(Base):
    __tablename__ = "storage_table_entities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "table_id"],
            ["storage_tables.tenant_id", "storage_tables.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "table_id", "partition_hash", "row_hash"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    table_id: Mapped[UUID]
    partition_key: Mapped[str] = mapped_column(Text)
    row_key: Mapped[str] = mapped_column(Text)
    partition_hash: Mapped[str] = mapped_column(String(64))
    row_hash: Mapped[str] = mapped_column(String(64))
    properties: Mapped[dict] = mapped_column(JSONB)
    timestamp_text: Mapped[str] = mapped_column(String(32))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    etag: Mapped[str] = mapped_column(String(80))


class TableCursor(Base):
    __tablename__ = "storage_table_cursors"
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
    table_id: Mapped[UUID | None]
    generation: Mapped[int]
    query: Mapped[dict] = mapped_column(JSONB)
    last_key: Mapped[list] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

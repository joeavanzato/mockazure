"""Versioned, tenant-scoped security resources and durable read snapshots."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class CollectionState(Base):
    __tablename__ = "collection_states"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)


class CollectionRecord(Base):
    __tablename__ = "collection_records"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    family: Mapped[str] = mapped_column(String(40), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    valid_from: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    valid_to: Mapped[int | None] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSONB)
    times: Mapped[dict] = mapped_column(JSONB)


class CollectionCursor(Base):
    __tablename__ = "collection_cursors"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    revision: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[dict] = mapped_column(JSONB)

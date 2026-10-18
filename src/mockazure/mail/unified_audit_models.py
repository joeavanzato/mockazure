"""Tenant-bound, materialized Exchange audit search sessions."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class UnifiedAuditSession(Base):
    __tablename__ = "mail_unified_audit_sessions"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))
    generation: Mapped[UUID]
    fingerprint: Mapped[str] = mapped_column(String(64))
    first_mode: Mapped[str] = mapped_column(String(32))
    mixed_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    record_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("position >= 0 AND record_count BETWEEN 0 AND 50000"),
        Index("ix_unified_audit_expiry", "tenant_id", "expires_at"),
    )


class UnifiedAuditItem(Base):
    __tablename__ = "mail_unified_audit_items"
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["mail_unified_audit_sessions.tenant_id", "mail_unified_audit_sessions.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("position > 0"),
    )

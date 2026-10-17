from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class AuditRole(Base):
    __tablename__ = "audit_roles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
        CheckConstraint("role IN ('Audit Logs','View-Only Audit Logs')"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)


class AuditQuery(Base):
    __tablename__ = "audit_queries"
    __table_args__ = (Index("audit_query_list_idx", "tenant_id", "created_at", "id"),)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), unique=True)
    generation: Mapped[UUID]
    revision: Mapped[int] = mapped_column(BigInteger)
    evidence_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    retention_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    filters: Mapped[dict] = mapped_column(JSONB)
    services: Mapped[list] = mapped_column(JSONB)
    record_count: Mapped[int | None] = mapped_column(BigInteger)


class AuditQueryItem(Base):
    __tablename__ = "audit_query_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "query_id"],
            ["audit_queries.tenant_id", "audit_queries.id"],
            ondelete="CASCADE",
        ),
        Index("audit_query_page_idx", "tenant_id", "query_id", "event_time", "record_id"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    query_id: Mapped[UUID] = mapped_column(primary_key=True)
    record_id: Mapped[UUID] = mapped_column(primary_key=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB)

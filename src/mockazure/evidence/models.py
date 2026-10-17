"""Immutable facts, retained receipts and versioned evidence views."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class EvidenceState(Base):
    __tablename__ = "evidence_state"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    generation: Mapped[UUID] = mapped_column(default=uuid4)
    source_revision: Mapped[int] = mapped_column(BigInteger, default=0)
    publication_revision: Mapped[int] = mapped_column(BigInteger, default=0)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    legacy_directory_revision: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    legacy_auth_cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvidenceSource(Base):
    __tablename__ = "evidence_sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_revision"),
        Index(
            "ix_evidence_sources_pending",
            "tenant_id",
            "source_revision",
            postgresql_where=text("acknowledged_at IS NULL"),
        ),
        CheckConstraint("available_at >= occurred_at AND source_revision > 0"),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    generation: Mapped[UUID]
    source_revision: Mapped[int] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    facts: Mapped[dict] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvidenceObservation(Base):
    __tablename__ = "evidence_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "source_kind", "source_id"],
            ["evidence_sources.tenant_id", "evidence_sources.kind", "evidence_sources.id"],
        ),
        CheckConstraint("family IN ('signIn','directoryAudit','riskDetection','riskyUser')"),
        CheckConstraint("available_at >= occurred_at"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    family: Mapped[str] = mapped_column(String(24), primary_key=True)
    record_id: Mapped[UUID] = mapped_column(primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[UUID]
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    subject_id: Mapped[UUID | None]
    payload: Mapped[dict] = mapped_column(JSONB)


class EvidenceVersion(Base):
    __tablename__ = "evidence_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "family", "record_id"],
            [
                "evidence_observations.tenant_id",
                "evidence_observations.family",
                "evidence_observations.record_id",
            ],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_kind", "source_id"],
            ["evidence_sources.tenant_id", "evidence_sources.kind", "evidence_sources.id"],
        ),
        CheckConstraint("valid_from > 0 AND (valid_to IS NULL OR valid_to > valid_from)"),
        Index(
            "uq_evidence_current",
            "tenant_id",
            "generation",
            "family",
            "record_id",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
        ),
        Index("ix_evidence_time", "tenant_id", "family", "event_time", "record_id"),
        Index("ix_evidence_user", "tenant_id", "family", "subject_id", "event_time", "record_id"),
        Index("ix_evidence_app", "tenant_id", "family", "app_id", "event_time", "record_id"),
        Index("ix_evidence_type", "tenant_id", "family", "event_type", "event_time", "record_id"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    family: Mapped[str] = mapped_column(String(24), primary_key=True)
    record_id: Mapped[UUID] = mapped_column(primary_key=True)
    generation: Mapped[UUID]
    source_kind: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[UUID]
    valid_from: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    valid_to: Mapped[int | None] = mapped_column(BigInteger)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    subject_id: Mapped[UUID | None]
    app_id: Mapped[str | None] = mapped_column(String(64))
    event_type: Mapped[str | None] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB)


class ProjectionReceipt(Base):
    __tablename__ = "evidence_projection_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "source_kind", "source_id"],
            ["evidence_sources.tenant_id", "evidence_sources.kind", "evidence_sources.id"],
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    generation: Mapped[UUID]
    revision: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24))
    record_ids: Mapped[list] = mapped_column(JSONB)


class EvidenceCursor(Base):
    __tablename__ = "evidence_cursors"
    __table_args__ = (Index("ix_evidence_cursor_expiry", "tenant_id", "expires_at"),)
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    generation: Mapped[UUID]
    revision: Mapped[int] = mapped_column(BigInteger)
    evidence_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    retention_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[dict] = mapped_column(JSONB)


class RiskActionCutoff(Base):
    __tablename__ = "evidence_risk_cutoffs"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    generation: Mapped[UUID]
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_revision: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB)

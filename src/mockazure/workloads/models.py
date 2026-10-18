"""Shared durable metadata, relationships, authority and local capabilities."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class WorkloadScope(Base):
    __tablename__ = "workload_scopes"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    workload: Mapped[str] = mapped_column(String(32), primary_key=True)
    scope: Mapped[str] = mapped_column(String(512), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    generation: Mapped[UUID] = mapped_column(default=uuid4)
    grant_revision: Mapped[int] = mapped_column(BigInteger, default=0)


class WorkloadVersion(Base):
    __tablename__ = "workload_versions"
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    workload: Mapped[str] = mapped_column(String(32), primary_key=True)
    scope: Mapped[str] = mapped_column(String(512), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(512), primary_key=True)
    valid_from: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    valid_to: Mapped[int | None] = mapped_column(BigInteger)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict] = mapped_column(JSONB)
    content_id: Mapped[UUID | None]
    name: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkloadRelation(Base):
    __tablename__ = "workload_relations"
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    workload: Mapped[str] = mapped_column(String(32), primary_key=True)
    scope: Mapped[str] = mapped_column(String(512), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    relation: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    valid_from: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    valid_to: Mapped[int | None] = mapped_column(BigInteger)


class WorkloadGrant(Base):
    __tablename__ = "workload_grants"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    workload: Mapped[str] = mapped_column(String(32))
    scope: Mapped[str] = mapped_column(String(512))
    principal_kind: Mapped[str] = mapped_column(String(16))
    principal_id: Mapped[UUID]
    role: Mapped[str] = mapped_column(String(16))
    display_name: Mapped[str | None] = mapped_column(String(256))


class WorkloadCursor(Base):
    __tablename__ = "workload_cursors"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    workload: Mapped[str] = mapped_column(String(32))
    state: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkloadDownload(Base):
    __tablename__ = "workload_downloads"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    content_id: Mapped[UUID]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

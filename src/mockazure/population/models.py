"""Durable population snapshots, cursors and cross-run operation receipts."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base
from mockazure.population.types import Input

ACTIVE = ("pending", "running", "verifying")


class BundleHead(Base):
    __tablename__ = "population_bundle_heads"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    revision: Mapped[int]


class BundleVersion(Base):
    __tablename__ = "population_bundle_versions"
    id: Mapped[UUID] = mapped_column(ForeignKey("population_bundle_heads.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    encrypted: Mapped[str] = mapped_column(Text)


class PopulationPreview(Base):
    __tablename__ = "population_previews"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    digest: Mapped[str] = mapped_column(String(64))
    state_digest: Mapped[str] = mapped_column(String(64))
    encrypted: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[UUID | None]


class PopulationRun(Base):
    __tablename__ = "population_runs"
    __table_args__ = (
        Index(
            "uq_population_active_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("state IN ('pending','running','verifying')"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"))
    namespace: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(24), default="pending")
    resolved_digest: Mapped[str] = mapped_column(String(64))
    encrypted: Mapped[str] = mapped_column(Text)
    cursor: Mapped[int] = mapped_column(default=0)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    progress: Mapped[dict] = mapped_column(JSONB, default=dict)
    errors: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PopulationStep(Base):
    __tablename__ = "population_steps"
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("population_runs.id", ondelete="CASCADE"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128))
    family: Mapped[str] = mapped_column(String(128))
    count: Mapped[int]
    offset: Mapped[int] = mapped_column(default=0)
    state: Mapped[str] = mapped_column(String(24), default="pending")


class ResourceBinding(Base):
    __tablename__ = "population_bindings"
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    namespace: Mapped[str] = mapped_column(String(128), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    actual_id: Mapped[str] = mapped_column(Text)


class OrganizationNamespace(Base):
    __tablename__ = "organization_namespaces"
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    namespace: Mapped[str] = mapped_column(String(128), primary_key=True)
    generation: Mapped[UUID]


class EntityBindingRow(Base):
    __tablename__ = "organization_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "namespace"],
            ["organization_namespaces.tenant_id", "organization_namespaces.namespace"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_organization_binding_parent",
            "tenant_id",
            "namespace",
            "service",
            text("md5(actual_id)"),
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    namespace: Mapped[str] = mapped_column(String(128), primary_key=True)
    logical_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    service: Mapped[str] = mapped_column(String(64), primary_key=True)
    actual_id: Mapped[str] = mapped_column(String(2048))
    parent_id: Mapped[str | None] = mapped_column(String(2048))
    generation: Mapped[UUID]


class OperationReceipt(Base):
    __tablename__ = "population_receipts"
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    namespace: Mapped[str] = mapped_column(String(128), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    family: Mapped[str] = mapped_column(String(128))
    input_digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24))
    encrypted_result: Mapped[str | None] = mapped_column(Text)
    origin_run: Mapped[UUID]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RunView(Input):
    id: UUID
    tenant_id: UUID
    state: Literal[
        "pending", "running", "verifying", "succeeded", "failed", "needs-attention", "cancelled"
    ]
    progress: dict[str, int]
    steps: dict[str, int]
    errors: list[str] = Field(default_factory=list)
    manifest_digest: str
    activity: dict | None = None

"""Diagnostic configuration and immutable per-destination outbox snapshots."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
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


class DiagnosticSetting(Base):
    __tablename__ = "monitor_diagnostic_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        Index(
            "uq_diagnostic_name",
            "tenant_id",
            "resource_key",
            "name_key",
            unique=True,
            postgresql_where=text("NOT deleted"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("cloud_state.tenant_id", ondelete="CASCADE"), index=True
    )
    resource_key: Mapped[str] = mapped_column(String(2048))
    resource_id: Mapped[str] = mapped_column(String(2048))
    name: Mapped[str] = mapped_column(String(260))
    name_key: Mapped[str] = mapped_column(String(260))
    properties: Mapped[dict] = mapped_column(JSONB)
    destinations: Mapped[list] = mapped_column(JSONB)
    deleted: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DiagnosticDelivery(Base):
    __tablename__ = "monitor_diagnostic_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "id"], ["jobs.tenant_id", "jobs.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "setting_id"],
            ["monitor_diagnostic_settings.tenant_id", "monitor_diagnostic_settings.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "setting_id", "source_id", "destination_kind"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    setting_id: Mapped[UUID]
    source_id: Mapped[UUID]
    generation: Mapped[int]
    destination_kind: Mapped[str] = mapped_column(String(20))
    destination: Mapped[dict] = mapped_column(JSONB)
    event: Mapped[dict] = mapped_column(JSONB)

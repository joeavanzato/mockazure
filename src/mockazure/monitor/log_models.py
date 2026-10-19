"""Workspace-bound immutable typed records, with idempotent source identities."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class LogRecord(Base):
    __tablename__ = "monitor_log_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["monitor_workspaces.tenant_id", "monitor_workspaces.customer_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "workspace_id", "table_name", "source_id"),
        Index(
            "ix_monitor_log_scan", "tenant_id", "workspace_id", "table_name", "time_generated", "id"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    workspace_id: Mapped[UUID]
    table_name: Mapped[str] = mapped_column(String(80))
    source_id: Mapped[str] = mapped_column(String(512))
    origin: Mapped[str] = mapped_column(String(20), default="import")
    time_generated: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB)

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class HuntingRecord(Base):
    __tablename__ = "hunting_records"
    __table_args__ = (
        Index("ix_hunting_time", "tenant_id", "table_name", "timestamp"),
        Index("ix_hunting_account", "tenant_id", "account_id", "timestamp"),
        Index("ix_hunting_device", "tenant_id", "device_id", "timestamp"),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    table_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    account_id: Mapped[str | None] = mapped_column(String(512))
    device_id: Mapped[str | None] = mapped_column(String(512))
    payload: Mapped[dict] = mapped_column(JSONB)
    provenance: Mapped[dict] = mapped_column(JSONB)
    digest: Mapped[str] = mapped_column(String(64))

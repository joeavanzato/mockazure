"""Credential-bearing state is never stored in generic ARM inventory."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class StorageAccount(Base):
    __tablename__ = "storage_accounts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "scope_key"],
            ["arm_resources.tenant_id", "arm_resources.scope_key"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("name"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(2048), primary_key=True)
    name: Mapped[str] = mapped_column(String(24))
    key1: Mapped[str] = mapped_column(Text)
    key2: Mapped[str] = mapped_column(Text)
    key1_created: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    key2_created: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    services: Mapped[dict] = mapped_column(JSONB, default=dict)

"""Provider registration and generation-bound internal publication facts."""

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


class CloudState(Base):
    __tablename__ = "cloud_state"
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    generation: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1")


class ProviderRegistration(Base):
    __tablename__ = "cloud_provider_registrations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "subscription_id"],
            ["arm_subscriptions.tenant_id", "arm_subscriptions.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("namespace_key = lower(namespace_key)"),
        CheckConstraint("state IN ('Registered','NotRegistered')"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    subscription_id: Mapped[UUID] = mapped_column(primary_key=True)
    namespace_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    state: Mapped[str] = mapped_column(String(24))


class CloudEvent(Base):
    __tablename__ = "cloud_service_events"
    __table_args__ = (
        Index(
            "ix_cloud_event_publication",
            "tenant_id",
            "generation",
            "publish_eligible",
            "occurred_at",
            "id",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("cloud_state.tenant_id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    generation: Mapped[int] = mapped_column(BigInteger)
    resource_id: Mapped[str] = mapped_column(String(2048))
    service: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(128))
    operation: Mapped[str] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB)
    publish_eligible: Mapped[bool] = mapped_column(default=True)

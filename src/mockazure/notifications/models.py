"""Graph subscription authority and durable basic-notification outbox."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class GraphSubscription(Base):
    __tablename__ = "graph_subscriptions"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    client_id: Mapped[UUID]
    subject_id: Mapped[UUID | None]
    actor: Mapped[dict] = mapped_column(JSONB)
    target: Mapped[dict] = mapped_column(JSONB)
    receiver_bindings: Mapped[dict] = mapped_column(JSONB)
    payload: Mapped[dict] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(BigInteger, default=1)
    deleted: Mapped[bool] = mapped_column(default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GraphNotification(Base):
    __tablename__ = "graph_notifications"
    __table_args__ = (
        UniqueConstraint("subscription_id", "source_key", "change_type"),
        ForeignKeyConstraint(
            ["tenant_id", "subscription_id"],
            ["graph_subscriptions.tenant_id", "graph_subscriptions.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "id"], ["jobs.tenant_id", "jobs.id"], ondelete="CASCADE"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    subscription_id: Mapped[UUID] = mapped_column(index=True)
    subscription_revision: Mapped[int] = mapped_column(BigInteger)
    source_key: Mapped[str] = mapped_column(String(256))
    change_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(default=0)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))

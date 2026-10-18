from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class ActivityState(Base):
    __tablename__ = "activity_states"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    generation: Mapped[UUID] = mapped_column(default=uuid4)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)


class Subscription(Base):
    __tablename__ = "activity_subscriptions"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    client_id: Mapped[UUID] = mapped_column(primary_key=True)
    content_type: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    generation: Mapped[UUID] = mapped_column(default=uuid4)
    start_revision: Mapped[int] = mapped_column(BigInteger)


class Content(Base):
    __tablename__ = "activity_contents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "generation", "record_id"),
        Index("ix_activity_publication", "tenant_id", "content_type", "published_at", "revision"),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    generation: Mapped[UUID]
    record_id: Mapped[UUID]
    content_type: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(BigInteger)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    digest: Mapped[str] = mapped_column(String(64))
    records: Mapped[list] = mapped_column(JSONB)


class Cursor(Base):
    __tablename__ = "activity_cursors"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    client_id: Mapped[UUID]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    state: Mapped[dict] = mapped_column(JSONB)


class Quota(Base):
    __tablename__ = "activity_quotas"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    publisher_id: Mapped[UUID] = mapped_column(primary_key=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requests: Mapped[int] = mapped_column(Integer)

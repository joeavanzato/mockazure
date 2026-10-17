from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, ForeignKeyConstraint, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class SecurityObject(Base):
    __tablename__ = "security_objects"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)


class SecurityOwner(Base):
    __tablename__ = "security_owners"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    object_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(primary_key=True)


class SecuritySnapshot(Base):
    __tablename__ = "security_snapshots"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    binding: Mapped[dict] = mapped_column(JSONB)
    query: Mapped[dict] = mapped_column(JSONB)
    signing_secret: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SecuritySnapshotItem(Base):
    __tablename__ = "security_snapshot_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["security_snapshots.tenant_id", "security_snapshots.id"],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    snapshot_id: Mapped[UUID] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)

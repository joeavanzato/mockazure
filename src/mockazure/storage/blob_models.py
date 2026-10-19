"""Blob identity, immutable versions, staged blocks and opaque list handles."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class Blob(Base):
    __tablename__ = "storage_blobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "account_id", "service", "container"],
            [
                "storage_namespaces.tenant_id",
                "storage_namespaces.account_id",
                "storage_namespaces.service",
                "storage_namespaces.name",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "account_id", "container", "name_hash"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[str] = mapped_column(String(2048))
    service: Mapped[str] = mapped_column(String(8), default="blob")
    container: Mapped[str] = mapped_column(String(63))
    name: Mapped[str] = mapped_column(String(1024))
    name_hash: Mapped[str] = mapped_column(String(64))
    current_id: Mapped[UUID | None]
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BlobVersion(Base):
    __tablename__ = "storage_blob_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "blob_id"],
            ["storage_blobs.tenant_id", "storage_blobs.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "content_id"], ["content_objects.tenant_id", "content_objects.id"]
        ),
        UniqueConstraint("tenant_id", "blob_id", "stamp", "kind"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    blob_id: Mapped[UUID]
    content_id: Mapped[UUID]
    stamp: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(12), default="version")
    etag: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)
    blocks: Mapped[list] = mapped_column(JSONB, default=list)


class BlobBlock(Base):
    __tablename__ = "storage_blob_blocks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "blob_id"],
            ["storage_blobs.tenant_id", "storage_blobs.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "content_id"], ["content_objects.tenant_id", "content_objects.id"]
        ),
        UniqueConstraint("tenant_id", "blob_id", "block_id"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    blob_id: Mapped[UUID]
    block_id: Mapped[str] = mapped_column(String(88))
    content_id: Mapped[UUID]
    size: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BlobLease(Base):
    __tablename__ = "storage_blob_leases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "blob_id"],
            ["storage_blobs.tenant_id", "storage_blobs.id"],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    blob_id: Mapped[UUID] = mapped_column(primary_key=True)
    lease_id: Mapped[UUID]
    duration: Mapped[int]
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    break_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etag: Mapped[str] = mapped_column(String(64))


class BlobCursor(Base):
    __tablename__ = "storage_blob_cursors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "account_id", "service", "container"],
            [
                "storage_namespaces.tenant_id",
                "storage_namespaces.account_id",
                "storage_namespaces.service",
                "storage_namespaces.name",
            ],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[str] = mapped_column(String(2048))
    service: Mapped[str] = mapped_column(String(8), default="blob")
    container: Mapped[str] = mapped_column(String(63))
    generation: Mapped[int] = mapped_column(BigInteger)
    query: Mapped[dict] = mapped_column(JSONB)
    last_key: Mapped[list] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

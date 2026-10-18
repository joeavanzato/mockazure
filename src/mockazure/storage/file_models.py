"""Share trees, immutable file references and reset-bound cursors."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class FileTree(Base):
    __tablename__ = "storage_file_trees"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "account_id", "service", "share"],
            [
                "storage_namespaces.tenant_id",
                "storage_namespaces.account_id",
                "storage_namespaces.service",
                "storage_namespaces.name",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "account_id", "share", "stamp"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[str] = mapped_column(String(2048))
    service: Mapped[str] = mapped_column(String(8), default="file")
    share: Mapped[str] = mapped_column(String(63))
    stamp: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)


class FileNode(Base):
    __tablename__ = "storage_file_nodes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "tree_id"],
            ["storage_file_trees.tenant_id", "storage_file_trees.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "content_id"], ["content_objects.tenant_id", "content_objects.id"]
        ),
        UniqueConstraint("tenant_id", "tree_id", "path_hash"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tree_id: Mapped[UUID]
    path: Mapped[str] = mapped_column(String(2048))
    path_key: Mapped[str] = mapped_column(String(2048))
    path_hash: Mapped[str] = mapped_column(String(64))
    parent_hash: Mapped[str] = mapped_column(String(64))
    directory: Mapped[bool]
    public_id: Mapped[UUID] = mapped_column(default=uuid4)
    version_id: Mapped[UUID] = mapped_column(default=uuid4)
    content_id: Mapped[UUID | None]
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    ranges: Mapped[list] = mapped_column(JSONB, default=list)
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    etag: Mapped[str] = mapped_column(String(64))
    lease_id: Mapped[UUID | None]
    lease_state: Mapped[str] = mapped_column(String(12), default="available")


class FileCursor(Base):
    __tablename__ = "storage_file_cursors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "tree_id"],
            ["storage_file_trees.tenant_id", "storage_file_trees.id"],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tree_id: Mapped[UUID]
    generation: Mapped[int] = mapped_column(BigInteger)
    query: Mapped[dict] = mapped_column(JSONB)
    last_key: Mapped[str] = mapped_column(String(2048))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

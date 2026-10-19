from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    LargeBinary,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base

CHUNK_SIZE = 1024 * 1024
DEFAULT_MAX_SIZE = 256 * CHUNK_SIZE


@dataclass(frozen=True)
class ContentRef:
    id: UUID
    size: int
    sha256: str
    media_type: str


class ContentObject(Base):
    __tablename__ = "content_objects"
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(12), default="staged")
    canonical_id: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("size >= 0"),
        CheckConstraint("state IN ('staged','finalized','alias')"),
    )


class ContentChunk(Base):
    __tablename__ = "content_chunks"
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    content_id: Mapped[UUID] = mapped_column(primary_key=True)
    ordinal: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "content_id"],
            ["content_objects.tenant_id", "content_objects.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("ordinal >= 0 AND octet_length(data) BETWEEN 1 AND 1048576"),
    )


class ContentReference(Base):
    __tablename__ = "content_references"
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    content_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "content_id"], ["content_objects.tenant_id", "content_objects.id"]
        ),
    )


class StreamUpload(Base):
    __tablename__ = "content_stream_uploads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "id"],
            ["content_objects.tenant_id", "content_objects.id"],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    expected_length: Mapped[int | None] = mapped_column(BigInteger)
    max_size: Mapped[int] = mapped_column(BigInteger)
    generation: Mapped[int] = mapped_column(BigInteger)

"""Current relationships, public versions and durable evidence facts."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class DirectoryState(Base):
    __tablename__ = "directory_state"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    generation: Mapped[UUID] = mapped_column(default=uuid4)


class Group(Base):
    __tablename__ = "directory_groups"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    mail_nickname: Mapped[str] = mapped_column(String(64))
    profile: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DirectoryEntry(Base):
    __tablename__ = "directory_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", "kind"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "group_id"], ["directory_groups.tenant_id", "directory_groups.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "sp_id"],
            ["identity_service_principals.tenant_id", "identity_service_principals.id"],
        ),
        CheckConstraint(
            "(kind='user' AND user_id=id AND group_id IS NULL AND sp_id IS NULL "
            "AND user_id IS NOT NULL)"
            " OR (kind='group' AND group_id=id AND user_id IS NULL AND sp_id IS NULL "
            "AND group_id IS NOT NULL)"
            " OR (kind='servicePrincipal' AND sp_id=id AND user_id IS NULL AND group_id IS NULL "
            "AND sp_id IS NOT NULL)"
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    user_id: Mapped[UUID | None]
    group_id: Mapped[UUID | None]
    sp_id: Mapped[UUID | None]


class DirectoryRelation(Base):
    __tablename__ = "directory_relations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "group_id"], ["directory_groups.tenant_id", "directory_groups.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "object_id"], ["directory_entries.tenant_id", "directory_entries.id"]
        ),
        CheckConstraint("relation IN ('member','owner')"),
        Index("ix_directory_relations_target", "tenant_id", "object_id", "relation"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    group_id: Mapped[UUID] = mapped_column(primary_key=True)
    object_id: Mapped[UUID] = mapped_column(primary_key=True)
    relation: Mapped[str] = mapped_column(String(12), primary_key=True)


class ObjectVersion(Base):
    __tablename__ = "directory_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "object_id", "kind"],
            ["directory_entries.tenant_id", "directory_entries.id", "directory_entries.kind"],
        ),
        CheckConstraint("valid_from > 0 AND (valid_to IS NULL OR valid_to > valid_from)"),
        Index("ix_directory_versions_round", "tenant_id", "kind", "valid_from", "object_id"),
        Index("ix_directory_versions_name", "tenant_id", "kind", "display_name", "object_id"),
        Index(
            "uq_directory_versions_current",
            "tenant_id",
            "object_id",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    object_id: Mapped[UUID] = mapped_column(primary_key=True)
    valid_from: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    valid_to: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(32))
    properties: Mapped[dict] = mapped_column(JSONB)
    changed_fields: Mapped[list] = mapped_column(JSONB)
    removed: Mapped[bool] = mapped_column(default=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    upn: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    account_enabled: Mapped[bool | None]
    mail_nickname: Mapped[str | None] = mapped_column(String(64))


class RelationVersion(Base):
    __tablename__ = "directory_relation_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "group_id"], ["directory_groups.tenant_id", "directory_groups.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "object_id"], ["directory_entries.tenant_id", "directory_entries.id"]
        ),
        CheckConstraint("relation IN ('member','owner')"),
        CheckConstraint("valid_from > 0 AND (valid_to IS NULL OR valid_to > valid_from)"),
        Index("ix_directory_relation_versions_target", "tenant_id", "object_id", "valid_from"),
        Index(
            "uq_directory_relation_versions_current",
            "tenant_id",
            "group_id",
            "object_id",
            "relation",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    group_id: Mapped[UUID] = mapped_column(primary_key=True)
    object_id: Mapped[UUID] = mapped_column(primary_key=True)
    relation: Mapped[str] = mapped_column(String(12), primary_key=True)
    valid_from: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    valid_to: Mapped[int | None] = mapped_column(BigInteger)


class EvidenceOutbox(Base):
    __tablename__ = "directory_evidence_outbox"
    __table_args__ = (
        UniqueConstraint("tenant_id", "revision"),
        Index(
            "ix_directory_outbox_pending",
            "tenant_id",
            "occurred_at",
            postgresql_where=text("projected_at IS NULL"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    revision: Mapped[int] = mapped_column(BigInteger)
    object_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(32))
    facts: Mapped[dict] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    projected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Continuation(Base):
    __tablename__ = "directory_continuations"
    __table_args__ = (Index("ix_directory_continuations_expiry", "tenant_id", "expires_at"),)
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    state: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

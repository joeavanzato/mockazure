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


class StorageNamespace(Base):
    __tablename__ = "storage_namespaces"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["storage_accounts.tenant_id", "storage_accounts.scope_key"],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(2048), primary_key=True)
    service: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(63), primary_key=True)
    etag: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)
    policies: Mapped[list] = mapped_column(JSONB, default=list)


class StorageDelegationKey(Base):
    __tablename__ = "storage_delegation_keys"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["storage_accounts.tenant_id", "storage_accounts.scope_key"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "account_id", "object_id", "starts_at", "expires_at", "version"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    account_id: Mapped[str] = mapped_column(String(2048))
    object_id: Mapped[UUID]
    client_id: Mapped[UUID]
    user_id: Mapped[UUID | None]
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    version: Mapped[str] = mapped_column(String(10))
    secret: Mapped[str] = mapped_column(Text)

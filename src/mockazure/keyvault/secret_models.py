"""Versioned encrypted secret state and opaque list continuations."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class Secret(Base):
    __tablename__ = "keyvault_secrets"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        UniqueConstraint("tenant_id", "vault_id", "name_key"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    vault_id: Mapped[str] = mapped_column(String(2048))
    name: Mapped[str] = mapped_column(String(127))
    name_key: Mapped[str] = mapped_column(String(127))
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SecretVersion(Base):
    __tablename__ = "keyvault_secret_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "secret_id"],
            ["keyvault_secrets.tenant_id", "keyvault_secrets.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "secret_id", "sequence"),
        UniqueConstraint("tenant_id", "secret_id", "version"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    secret_id: Mapped[UUID]
    sequence: Mapped[int] = mapped_column(BigInteger)
    version: Mapped[str] = mapped_column(String(32), default=lambda: uuid4().hex)
    encrypted_value: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSONB)
    tags: Mapped[dict] = mapped_column(JSONB)
    content_type: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SecretCursor(Base):
    __tablename__ = "keyvault_secret_cursors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "vault_id"],
            ["keyvault_vaults.tenant_id", "keyvault_vaults.scope_key"],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    vault_id: Mapped[str] = mapped_column(String(2048))
    generation: Mapped[int] = mapped_column(BigInteger)
    query: Mapped[dict] = mapped_column(JSONB)
    last_key: Mapped[str] = mapped_column(String(127))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeletedVault(Base):
    __tablename__ = "keyvault_deleted_vaults"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        UniqueConstraint("name"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    vault_id: Mapped[str] = mapped_column(String(2048), primary_key=True)
    name: Mapped[str] = mapped_column(String(24))
    display_id: Mapped[str] = mapped_column(String(2048))
    location: Mapped[str] = mapped_column(String(100))
    tags: Mapped[dict] = mapped_column(JSONB)
    payload: Mapped[dict] = mapped_column(JSONB)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purge_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

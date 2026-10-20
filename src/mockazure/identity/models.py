from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class User(Base):
    password_change_required: Mapped[bool] = mapped_column(default=False, server_default="false")
    credential_version: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    __tablename__ = "identity_users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "upn"),
        ForeignKeyConstraint(
            ["tenant_id", "manager_id"],
            ["identity_users.tenant_id", "identity_users.id"],
            name="fk_identity_user_manager",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    upn: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(200))
    verifier: Mapped[str] = mapped_column(String(256))
    enabled: Mapped[bool] = mapped_column(default=True)
    profile: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sessions_valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manager_id: Mapped[UUID | None]


class Application(Base):
    __tablename__ = "identity_applications"
    __table_args__ = (UniqueConstraint("tenant_id", "client_id"),)
    client_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    object_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    profile: Mapped[dict] = mapped_column(JSONB, default=dict)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    client_mode: Mapped[str] = mapped_column(String(20))
    redirect_uris: Mapped[list] = mapped_column(JSONB, default=list)
    required_permissions: Mapped[list] = mapped_column(JSONB, default=list)
    enabled: Mapped[bool] = mapped_column(default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServicePrincipal(Base):
    __tablename__ = "identity_service_principals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "client_id"),
        UniqueConstraint("tenant_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    client_id: Mapped[UUID] = mapped_column(ForeignKey("identity_applications.client_id"))
    enabled: Mapped[bool] = mapped_column(default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile: Mapped[dict] = mapped_column(JSONB, default=dict)


class Credential(Base):
    __tablename__ = "identity_credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["identity_applications.tenant_id", "identity_applications.client_id"],
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    client_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(20))
    verifier: Mapped[str | None] = mapped_column(String(256))
    public_certificate: Mapped[str | None]
    thumbprint: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    display_name: Mapped[str | None] = mapped_column(String(200))
    owner_id: Mapped[UUID | None]
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Grant(Base):
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __tablename__ = "identity_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["identity_service_principals.tenant_id", "identity_service_principals.client_id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
        UniqueConstraint(
            "tenant_id",
            "client_id",
            "resource",
            "kind",
            "user_id",
            postgresql_nulls_not_distinct=True,
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    client_id: Mapped[UUID]
    resource: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(20))
    permissions: Mapped[list] = mapped_column(JSONB)
    user_id: Mapped[UUID | None]


class DirectoryRole(Base):
    __tablename__ = "identity_directory_roles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(80), primary_key=True)


class ServicePrincipalRole(Base):
    __tablename__ = "identity_service_principal_roles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["identity_service_principals.tenant_id", "identity_service_principals.client_id"],
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    client_id: Mapped[UUID] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(80), primary_key=True)

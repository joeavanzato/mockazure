from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class ManagedIdentity(Base):
    __tablename__ = "managed_identities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "id"],
            ["identity_service_principals.tenant_id", "identity_service_principals.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["identity_applications.tenant_id", "identity_applications.client_id"],
        ),
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "resource_key"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    client_id: Mapped[UUID] = mapped_column(unique=True)
    kind: Mapped[str] = mapped_column(String(10))
    resource_id: Mapped[str] = mapped_column(String(2048))
    resource_key: Mapped[str] = mapped_column(String(2048))


class ManagedHost(Base):
    __tablename__ = "managed_identity_hosts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "name"),
        ForeignKeyConstraint(
            ["tenant_id", "system_identity_id"],
            ["managed_identities.tenant_id", "managed_identities.id"],
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(default=True)
    header_hash: Mapped[str] = mapped_column(String(64), unique=True)
    system_identity_id: Mapped[UUID | None] = mapped_column(unique=True)


class HostIdentity(Base):
    __tablename__ = "managed_identity_host_users"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "host_id"],
            ["managed_identity_hosts.tenant_id", "managed_identity_hosts.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "identity_id"], ["managed_identities.tenant_id", "managed_identities.id"]
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    host_id: Mapped[UUID] = mapped_column(primary_key=True)
    identity_id: Mapped[UUID] = mapped_column(primary_key=True)

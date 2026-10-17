"""Tenant-owned ARM inventory and a single scoped assignment authority."""

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

READER_ID = "acdd72a7-3385-48ef-bd42-f606fba81ae7"


class Subscription(Base):
    __tablename__ = "arm_subscriptions"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(20), default="Enabled")
    tags: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class ArmResource(Base):
    __tablename__ = "arm_resources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "subscription_id", "scope_key"),
        ForeignKeyConstraint(
            ["tenant_id", "subscription_id"],
            ["arm_subscriptions.tenant_id", "arm_subscriptions.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "subscription_id", "group_key"],
            ["arm_resources.tenant_id", "arm_resources.subscription_id", "arm_resources.scope_key"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "deletion_job_id"],
            ["jobs.tenant_id", "jobs.id"],
            name="fk_arm_deletion_job",
        ),
        CheckConstraint("kind IN ('group','resource')"),
        CheckConstraint("scope_key = lower(scope_key)"),
        CheckConstraint(
            "left(scope_key, length(subscription_id::text)+16) = "
            "'/subscriptions/' || subscription_id::text || '/'"
        ),
        CheckConstraint(
            "(kind='group' AND group_key IS NULL) OR "
            "(kind='resource' AND group_key IS NOT NULL AND "
            "left(scope_key,length(group_key)+11)=group_key || '/providers/')"
        ),
        Index("ix_arm_inventory", "tenant_id", "subscription_id", "kind", "scope_key"),
        Index(
            "ix_arm_inventory_type", "tenant_id", "subscription_id", "resource_type", "scope_key"
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    subscription_id: Mapped[UUID]
    scope_key: Mapped[str] = mapped_column(String(2048), primary_key=True)
    display_id: Mapped[str] = mapped_column(String(2048))
    kind: Mapped[str] = mapped_column(String(16))
    group_key: Mapped[str | None] = mapped_column(String(2048))
    name: Mapped[str] = mapped_column(String(1024))
    resource_type: Mapped[str] = mapped_column(String(512))
    location: Mapped[str] = mapped_column(String(100))
    tags: Mapped[dict] = mapped_column(JSONB, default=dict)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    deletion_job_id: Mapped[UUID | None]
    provisioning_state: Mapped[str] = mapped_column(String(32), default="Succeeded")
    revision: Mapped[int] = mapped_column(BigInteger, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoleAssignment(Base):
    __tablename__ = "arm_role_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "subscription_id"],
            ["arm_subscriptions.tenant_id", "arm_subscriptions.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["identity_service_principals.tenant_id", "identity_service_principals.client_id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "group_id"], ["directory_groups.tenant_id", "directory_groups.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "subscription_id", "inventory_key"],
            ["arm_resources.tenant_id", "arm_resources.subscription_id", "arm_resources.scope_key"],
            ondelete="CASCADE",
        ),
        CheckConstraint("num_nonnulls(client_id,user_id,group_id)=1"),
        CheckConstraint(
            "(inventory_key IS NULL AND scope_key='/subscriptions/' || subscription_id::text)"
            " OR (inventory_key IS NOT NULL AND scope_key=inventory_key)"
        ),
        Index(
            "uq_arm_assignment",
            "tenant_id",
            "scope_key",
            "role_id",
            "client_id",
            "user_id",
            "group_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("NOT legacy"),
        ),
        Index("ix_arm_assignment_scope", "tenant_id", "scope_key", "role_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    subscription_id: Mapped[UUID] = mapped_column(index=True)
    client_id: Mapped[UUID | None]
    user_id: Mapped[UUID | None]
    group_id: Mapped[UUID | None]
    scope_key: Mapped[str] = mapped_column(String(2048))
    inventory_key: Mapped[str | None] = mapped_column(String(2048))
    role_id: Mapped[str] = mapped_column(String(36), default=READER_ID)
    legacy: Mapped[bool] = mapped_column(default=False, server_default="false")
    description: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Import compatibility only: no second table or source of permission.
ReaderAssignment = RoleAssignment

"""Workspace customer identity is durable and tenant-bound."""

from uuid import UUID, uuid4

from sqlalchemy import ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class Workspace(Base):
    __tablename__ = "monitor_workspaces"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "scope_key"],
            ["arm_resources.tenant_id", "arm_resources.scope_key"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("customer_id"),
        UniqueConstraint("tenant_id", "customer_id", name="uq_monitor_workspace_tenant_customer"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(2048), primary_key=True)
    customer_id: Mapped[UUID] = mapped_column(default=uuid4)

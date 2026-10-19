"""Authoritative Exchange role membership and explicit management assignments."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class ExchangeRoleMember(Base):
    __tablename__ = "mail_role_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "principal_id"],
            ["directory_entries.tenant_id", "directory_entries.id"],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    group_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_id: Mapped[UUID] = mapped_column(primary_key=True)


class ExchangeRoleAssignment(Base):
    __tablename__ = "mail_role_assignments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name_key"),
        ForeignKeyConstraint(
            ["tenant_id", "principal_id"],
            ["directory_entries.tenant_id", "directory_entries.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "recipient_group_id"],
            ["directory_groups.tenant_id", "directory_groups.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("(principal_id IS NULL) <> (group_name IS NULL)"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    name_key: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(64))
    principal_id: Mapped[UUID | None]
    group_name: Mapped[str | None] = mapped_column(String(64))
    recipient_group_id: Mapped[UUID | None]

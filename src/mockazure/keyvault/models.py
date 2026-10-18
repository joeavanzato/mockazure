"""Operational vault identity; canonical public configuration lives in ARM inventory."""

from uuid import UUID

from sqlalchemy import ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class Vault(Base):
    __tablename__ = "keyvault_vaults"
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

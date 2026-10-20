from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, ForeignKeyConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class HuntingSettings(Base):
    __tablename__ = "hunting_settings"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    entitlements: Mapped[list] = mapped_column(JSONB, default=list)


class HuntingVisibility(Base):
    __tablename__ = "hunting_visibility"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["identity_users.tenant_id", "identity_users.id"],
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    view_data: Mapped[bool] = mapped_column(Boolean, default=False)
    tables: Mapped[list] = mapped_column(JSONB, default=list)
    device_ids: Mapped[list | None] = mapped_column(JSONB)

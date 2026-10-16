from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class SubscribedSku(Base):
    __tablename__ = "licensing_skus"
    __table_args__ = (
        CheckConstraint("enabled >= consumed_units AND consumed_units >= 0"),
        CheckConstraint("suspended >= 0 AND warning >= 0"),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    sku_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(unique=True)
    sku_part_number: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[int] = mapped_column(Integer)
    suspended: Mapped[int] = mapped_column(Integer, default=0)
    warning: Mapped[int] = mapped_column(Integer, default=0)
    consumed_units: Mapped[int] = mapped_column(Integer, default=0)
    service_plans: Mapped[list] = mapped_column(JSONB)
    allowed_usage_locations: Mapped[list] = mapped_column(JSONB, default=list)


class UserLicense(Base):
    __tablename__ = "licensing_user_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "sku_id"], ["licensing_skus.tenant_id", "licensing_skus.sku_id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    sku_id: Mapped[UUID] = mapped_column(primary_key=True)
    disabled_plans: Mapped[list] = mapped_column(JSONB, default=list)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

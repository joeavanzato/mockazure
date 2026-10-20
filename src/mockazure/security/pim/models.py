from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class PimSchedule(Base):
    __tablename__ = "security_pim_schedules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "eligibility_id"],
            ["security_pim_schedules.tenant_id", "security_pim_schedules.id"],
        ),
        Index("ix_pim_effective", "tenant_id", "principal_id", "kind", "starts_at", "ends_at"),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    principal_id: Mapped[UUID]
    role: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eligibility_id: Mapped[UUID | None]
    payload: Mapped[dict] = mapped_column(JSONB)


class PimRequest(Base):
    __tablename__ = "security_pim_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "schedule_id"],
            ["security_pim_schedules.tenant_id", "security_pim_schedules.id"],
        ),
        Index("ix_pim_request_principal", "tenant_id", "principal_id", "kind"),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    principal_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(20))
    schedule_id: Mapped[UUID | None]
    payload: Mapped[dict] = mapped_column(JSONB)


class PimPolicy(Base):
    __tablename__ = "security_pim_policies"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(200), primary_key=True)
    rules: Mapped[dict] = mapped_column(JSONB)

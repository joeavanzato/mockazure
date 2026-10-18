from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class MethodPolicyState(Base):
    __tablename__ = "authentication_method_policy_state"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    enforced: Mapped[bool] = mapped_column(default=True)
    configurations: Mapped[dict] = mapped_column(JSONB, default=dict)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthenticationContext(Base):
    __tablename__ = "trusted_authentication_contexts"
    session_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    client_id: Mapped[UUID]
    user_id: Mapped[UUID | None]
    device_id: Mapped[UUID | None]
    platform: Mapped[str | None] = mapped_column(String(32))
    method_id: Mapped[str | None] = mapped_column(String(128))
    method_version: Mapped[str | None] = mapped_column(String(36))
    method_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

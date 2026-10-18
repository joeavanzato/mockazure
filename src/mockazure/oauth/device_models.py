from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, ForeignKeyConstraint, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class DeviceAuthorization(Base):
    credential_version: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    __tablename__ = "oauth_devices"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["identity_service_principals.tenant_id", "identity_service_principals.client_id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    client_id: Mapped[UUID]
    user_id: Mapped[UUID | None]
    common: Mapped[bool] = mapped_column(default=False)
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    user_code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    resource: Mapped[str] = mapped_column(String(30))
    scopes: Mapped[list] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20))
    mfa: Mapped[str] = mapped_column(String(20), default="not_performed")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    interval: Mapped[int] = mapped_column(default=5)
    last_poll: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthenticationOutcome(Base):
    __tablename__ = "identity_authentication_outcomes"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    client_id: Mapped[UUID]
    user_id: Mapped[UUID | None]
    flow: Mapped[str] = mapped_column(String(30))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    correlation_id: Mapped[UUID]
    result: Mapped[str] = mapped_column(String(40))
    mfa: Mapped[str] = mapped_column(String(20))

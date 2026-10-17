from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class AuthorizationTransaction(Base):
    __tablename__ = "oauth_authorizations"
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
    device_id: Mapped[UUID | None] = mapped_column(ForeignKey("oauth_devices.id"))
    common: Mapped[bool] = mapped_column(default=False)
    redirect_uri: Mapped[str] = mapped_column(String(2048))
    scopes: Mapped[list] = mapped_column(JSONB)
    resource: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(2048))
    nonce: Mapped[str] = mapped_column(String(2048))
    challenge: Mapped[str] = mapped_column(String(128))
    response_mode: Mapped[str] = mapped_column(String(20))
    cookie_hash: Mapped[str] = mapped_column(String(64))
    csrf_hash: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(20))
    mfa: Mapped[str] = mapped_column(String(20), default="not_performed")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    code_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginSession(Base):
    client_app_type: Mapped[str] = mapped_column(String(40), default="browser")
    __tablename__ = "oauth_sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["identity_service_principals.tenant_id", "identity_service_principals.client_id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    client_id: Mapped[UUID]
    user_id: Mapped[UUID]
    resource: Mapped[str] = mapped_column(String(30))
    scopes: Mapped[list] = mapped_column(JSONB)
    mfa: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshHandle(Base):
    __tablename__ = "oauth_refresh_handles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "session_id"], ["oauth_sessions.tenant_id", "oauth_sessions.id"]
        ),
    )
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[UUID]
    session_id: Mapped[UUID] = mapped_column(index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

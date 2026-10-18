from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class PasswordResetOperation(Base):
    __tablename__ = "authentication_password_resets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
        Index(
            "uq_authentication_reset_pending",
            "tenant_id",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('notStarted', 'running')"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID]
    client_id: Mapped[UUID]
    actor_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(24))
    status_detail: Mapped[str] = mapped_column(String(200))
    verifier: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

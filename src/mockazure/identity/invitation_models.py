from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class GuestInvitation(Base):
    __tablename__ = "identity_guest_invitations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
        Index("ix_guest_invitation_email", "tenant_id", "email"),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID]
    generation: Mapped[UUID]
    email: Mapped[str] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(32))
    redirect_url: Mapped[str] = mapped_column(String(2048))
    message_info: Mapped[dict] = mapped_column(JSONB)
    send_message: Mapped[bool]
    delivery: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GuestPolicy(Base):
    __tablename__ = "identity_guest_policies"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    allow_member_invites: Mapped[bool] = mapped_column(default=True)

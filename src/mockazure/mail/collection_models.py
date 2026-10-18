"""Typed local configuration and permission evidence; separate from command grants."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class ExchangeConfiguration(Base):
    __tablename__ = "mail_exchange_configuration"
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    organization: Mapped[dict] = mapped_column(JSONB, default=dict)
    transport: Mapped[dict] = mapped_column(JSONB, default=dict)


class ExchangeConfigurationRecord(Base):
    __tablename__ = "mail_exchange_configuration_records"
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    ordinal: Mapped[int]
    payload: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (CheckConstraint("kind IN ('acceptedDomain','transportRule')"),)


class ExchangePermissionRecord(Base):
    __tablename__ = "mail_exchange_permission_records"
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    family: Mapped[str] = mapped_column(String(10), primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    mailbox_user_id: Mapped[UUID] = mapped_column()
    ordinal: Mapped[int]
    payload: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "mailbox_user_id"],
            ["mail_mailboxes.tenant_id", "mail_mailboxes.user_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("family IN ('mailbox','recipient')"),
        Index("mail_exchange_permission_mailbox_idx", "tenant_id", "mailbox_user_id", "family"),
    )

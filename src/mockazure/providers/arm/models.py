from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class Subscription(Base):
    __tablename__ = "arm_subscriptions"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(20), default="Enabled")


class ReaderAssignment(Base):
    __tablename__ = "arm_reader_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "subscription_id"],
            ["arm_subscriptions.tenant_id", "arm_subscriptions.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["identity_service_principals.tenant_id", "identity_service_principals.client_id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
        CheckConstraint("(client_id IS NULL) <> (user_id IS NULL)"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    subscription_id: Mapped[UUID] = mapped_column(index=True)
    client_id: Mapped[UUID | None]
    user_id: Mapped[UUID | None]

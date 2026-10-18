"""Operational mailbox delegation is separate from imported ACL evidence."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class MailboxDelegation(Base):
    __tablename__ = "mail_delegations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "mailbox_id"],
            ["mail_mailboxes.tenant_id", "mail_mailboxes.user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "principal_id"],
            ["directory_entries.tenant_id", "directory_entries.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("permission IN ('FullAccess','SendAs','SendOnBehalf')"),
        CheckConstraint("inheritance IN ('None','All','Children','Descendents','SelfAndChildren')"),
        CheckConstraint("NOT deny OR permission = 'FullAccess'"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    mailbox_id: Mapped[UUID] = mapped_column(primary_key=True)
    principal_id: Mapped[UUID] = mapped_column(primary_key=True)
    permission: Mapped[str] = mapped_column(String(20), primary_key=True)
    deny: Mapped[bool] = mapped_column(primary_key=True, default=False)
    inheritance: Mapped[str] = mapped_column(String(20), primary_key=True, default="None")
    auto_mapping: Mapped[bool] = mapped_column(default=True)

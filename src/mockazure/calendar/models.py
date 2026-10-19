"""Canonical calendars, event history and fenced meeting publication."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKeyConstraint, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class CalendarState(Base):
    __tablename__ = "calendar_state"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["mail_mailboxes.tenant_id", "mail_mailboxes.user_id"],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    generation: Mapped[UUID] = mapped_column(default=uuid4)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    default_calendar_id: Mapped[str] = mapped_column(String(128))
    default_group_id: Mapped[str] = mapped_column(String(128))


class CalendarGroup(Base):
    __tablename__ = "calendar_groups"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["calendar_state.tenant_id", "calendar_state.user_id"],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)


class Calendar(Base):
    __tablename__ = "calendars"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id", "group_id"],
            ["calendar_groups.tenant_id", "calendar_groups.user_id", "calendar_groups.id"],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSONB)


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id", "calendar_id"],
            ["calendars.tenant_id", "calendars.user_id", "calendars.id"],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    calendar_id: Mapped[str] = mapped_column(String(128))
    meeting_id: Mapped[UUID]
    organizer_id: Mapped[UUID]
    revision: Mapped[int] = mapped_column(BigInteger)
    source_revision: Mapped[int] = mapped_column(BigInteger, default=0)
    deleted: Mapped[bool] = mapped_column(default=False)
    payload: Mapped[dict] = mapped_column(JSONB)
    transaction_client: Mapped[UUID | None]
    transaction_id: Mapped[str | None] = mapped_column(String(256))


class CalendarEventVersion(Base):
    __tablename__ = "calendar_event_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["calendar_state.tenant_id", "calendar_state.user_id"],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    calendar_id: Mapped[str] = mapped_column(String(128))
    deleted: Mapped[bool]
    payload: Mapped[dict] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MeetingDelivery(Base):
    __tablename__ = "calendar_meeting_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(["id"], ["jobs.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["calendar_state.tenant_id", "calendar_state.user_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    user_id: Mapped[UUID]
    generation: Mapped[UUID]
    evidence_generation: Mapped[UUID]
    cloud_generation: Mapped[int] = mapped_column(BigInteger)
    actor: Mapped[dict] = mapped_column(JSONB)
    operation: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str] = mapped_column(String(128))
    snapshot: Mapped[dict] = mapped_column(JSONB)


class CalendarException(Base):
    __tablename__ = "calendar_exceptions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id", "master_id"],
            ["calendar_events.tenant_id", "calendar_events.user_id", "calendar_events.id"],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    master_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    overrides: Mapped[dict] = mapped_column(JSONB)
    deleted: Mapped[bool]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

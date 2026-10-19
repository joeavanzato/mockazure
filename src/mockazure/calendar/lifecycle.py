"""Calendar fixture state excludes transient delivery authority and uses fresh generations."""

import copy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from sqlalchemy import delete, select, tuple_, update
from sqlalchemy.orm import aliased

from mockazure.admin.errors import AdminError
from mockazure.calendar.contracts import event_input, validate
from mockazure.calendar.models import (
    Calendar,
    CalendarEvent,
    CalendarEventVersion,
    CalendarException,
    CalendarGroup,
    CalendarState,
    MeetingDelivery,
)
from mockazure.db.job_model import Job
from mockazure.identity.types import IdentityInput
from mockazure.mail.models import Mailbox
from mockazure.providers.graph.errors import GraphError


class GroupInput(IdentityInput):
    id: str = Field(min_length=1, max_length=128)
    payload: dict


class CalendarInput(GroupInput):
    group_id: str = Field(min_length=1, max_length=128)


class EventInput(GroupInput):
    calendar_id: str = Field(min_length=1, max_length=128)
    meeting_id: UUID
    organizer_id: UUID
    deleted: bool = False
    source_revision: int = Field(default=0, ge=0)
    transaction_client: UUID | None = None
    transaction_id: str | None = Field(default=None, max_length=256)


class ExceptionInput(IdentityInput):
    master_id: str = Field(min_length=1, max_length=128)
    day: str
    overrides: dict
    deleted: bool = False
    updated_at: datetime


class OwnerInput(IdentityInput):
    user_id: UUID
    default_calendar_id: str
    default_group_id: str
    exceptions: list[ExceptionInput] = Field(default_factory=list, max_length=10000)
    groups: list[GroupInput] = Field(default_factory=list, max_length=1000)
    calendars: list[CalendarInput] = Field(default_factory=list, max_length=1000)
    events: list[EventInput] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def valid(self):
        groups = {g.id for g in self.groups}
        calendars = {c.id for c in self.calendars}
        if (
            len(groups) != len(self.groups)
            or len(calendars) != len(self.calendars)
            or len({e.id for e in self.events}) != len(self.events)
            or self.default_group_id not in groups
            or self.default_calendar_id not in calendars
            or any(c.group_id not in groups for c in self.calendars)
            or any(e.calendar_id not in calendars for e in self.events)
        ):
            raise ValueError("Invalid calendar parent relationships")
        try:
            for values, kind in [
                (self.groups, "calendarGroup"),
                (self.calendars, "calendar"),
                (self.events, "event"),
            ]:
                for value in values:
                    validate(kind, {**value.payload, "id": value.id})
        except GraphError:
            raise ValueError("Calendar payload violates Graph schema") from None
        from datetime import date

        from mockazure.calendar.contracts import EVENT_FIELDS
        from mockazure.calendar.exceptions import check_occurrence_boundary, occurrence_exists
        from mockazure.calendar.recurrence import occurrence_on

        masters = {e.id: e for e in self.events}
        if len({(e.master_id, e.day) for e in self.exceptions}) != len(self.exceptions):
            raise ValueError("Duplicate calendar exception")
        for entry in self.exceptions:
            day = date.fromisoformat(entry.day)
            if (
                entry.updated_at.tzinfo is None
                or entry.master_id not in masters
                or not masters[entry.master_id].payload.get("recurrence")
            ):
                raise ValueError("Invalid exception parent or timestamp")
            if entry.overrides.keys() - (
                EVENT_FIELDS - {"recurrence", "transactionId", "attendees"}
            ):
                raise ValueError("Invalid exception override")
            try:
                validate("event", entry.overrides)
                master = masters[entry.master_id]
                if not occurrence_exists(master.payload, day):
                    raise ValueError("Exception is not an occurrence of its master")
                base = occurrence_on({**master.payload, "id": master.id}, day)
                patch = event_input(entry.overrides, previous=base)
                check_occurrence_boundary(master.payload, day, {**base, **patch})
            except GraphError:
                raise ValueError("Invalid exception schema") from None
        transactions = set()
        for event in self.events:
            if not event.deleted:
                try:
                    event_input({}, previous=event.payload)
                except GraphError:
                    raise ValueError("Invalid event timing or recurrence") from None
            if (event.transaction_client is None) != (event.transaction_id is None):
                raise ValueError("Transaction client and identity must be paired")
            if event.transaction_id is not None:
                key = (event.calendar_id, event.transaction_client, event.transaction_id)
                if key in transactions:
                    raise ValueError("Duplicate calendar transaction identity")
                transactions.add(key)
        return self


class CalendarStateInput(IdentityInput):
    owners: list[OwnerInput] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def distinct(self):
        if len({o.user_id for o in self.owners}) != len(self.owners):
            raise ValueError("Duplicate calendar owner")
        return self


async def export_calendars(session, tenant):
    owners = []
    states = list(
        await session.scalars(
            select(CalendarState)
            .where(CalendarState.tenant_id == tenant)
            .order_by(CalendarState.user_id)
            .limit(10001)
        )
    )
    if len(states) > 10000:
        raise AdminError(413, "CalendarStateLimit", "Too many calendar owners")
    for state in states:
        document = {
            "user_id": state.user_id,
            "default_calendar_id": state.default_calendar_id,
            "default_group_id": state.default_group_id,
        }
        for key, cls in [
            ("groups", CalendarGroup),
            ("calendars", Calendar),
            ("events", CalendarEvent),
        ]:
            maximum = 10000 if key == "events" else 1000
            records = list(
                await session.scalars(
                    select(cls)
                    .where(cls.tenant_id == tenant, cls.user_id == state.user_id)
                    .order_by(cls.id)
                    .limit(maximum + 1)
                )
            )
            if len(records) > maximum:
                raise AdminError(413, "CalendarStateLimit", "Too many calendar objects")
            document[key] = []
            for row in records:
                value = {"id": row.id, "payload": copy.deepcopy(row.payload)}
                if key == "calendars":
                    value["group_id"] = row.group_id
                if key == "events":
                    value.update(
                        {
                            k: getattr(row, k)
                            for k in [
                                "calendar_id",
                                "meeting_id",
                                "organizer_id",
                                "deleted",
                                "source_revision",
                                "transaction_client",
                                "transaction_id",
                            ]
                        }
                    )
                document[key].append(value)
        exceptions = list(
            await session.scalars(
                select(CalendarException)
                .where(
                    CalendarException.tenant_id == tenant,
                    CalendarException.user_id == state.user_id,
                )
                .limit(10001)
            )
        )
        if len(exceptions) > 10000:
            raise AdminError(413, "CalendarStateLimit", "Too many calendar exceptions")
        document["exceptions"] = [
            {
                key: getattr(e, key)
                for key in ["master_id", "day", "overrides", "deleted", "updated_at"]
            }
            for e in exceptions
        ]
        owners.append(document)
    return CalendarStateInput.model_validate({"owners": owners})


async def replace_calendars(session, tenant, value):
    # Caller owns the shared lifecycle lock. Never acquire the tenant lock here
    # after the security/evidence/cloud locks, which would invert workflow order.
    for owner in value.owners:
        if await session.get(Mailbox, (tenant, owner.user_id)) is None:
            raise AdminError(422, "InvalidCalendarState", "Calendar owner mailbox was not found")
        for event in owner.events:
            if await session.get(Mailbox, (tenant, event.organizer_id)) is None:
                raise AdminError(
                    422, "InvalidCalendarState", "Meeting organizer mailbox was not found"
                )
    await session.execute(
        update(Job)
        .where(
            Job.tenant_id == tenant,
            Job.kind == "calendar.meeting",
            Job.state.in_(["pending", "running"]),
        )
        .values(
            state="failed",
            error_code="CalendarStateReplaced",
            claim_epoch=Job.claim_epoch + 1,
            lease_owner=None,
            lease_until=None,
            updated_at=datetime.now(UTC),
        )
    )
    await session.execute(delete(CalendarState).where(CalendarState.tenant_id == tenant))
    await session.flush()
    for owner in value.owners:
        state = CalendarState(
            tenant_id=tenant,
            user_id=owner.user_id,
            generation=uuid4(),
            revision=0,
            default_calendar_id=owner.default_calendar_id,
            default_group_id=owner.default_group_id,
        )
        session.add(state)
        await session.flush()
        for group in owner.groups:
            session.add(
                CalendarGroup(tenant_id=tenant, user_id=owner.user_id, **group.model_dump())
            )
        await session.flush()
        for calendar in owner.calendars:
            session.add(Calendar(tenant_id=tenant, user_id=owner.user_id, **calendar.model_dump()))
        await session.flush()
        for event in owner.events:
            state.revision += 1
            values = event.model_dump()
            # New generation revisions restart; old jobs are already fenced out.
            values["source_revision"] = 0
            session.add(
                CalendarEvent(
                    tenant_id=tenant, user_id=owner.user_id, revision=state.revision, **values
                )
            )
            session.add(
                CalendarEventVersion(
                    tenant_id=tenant,
                    user_id=owner.user_id,
                    id=event.id,
                    revision=state.revision,
                    calendar_id=event.calendar_id,
                    deleted=event.deleted,
                    payload=event.payload,
                    occurred_at=datetime.now(UTC),
                )
            )
        await session.flush()
        for entry in owner.exceptions:
            session.add(
                CalendarException(tenant_id=tenant, user_id=owner.user_id, **entry.model_dump())
            )
        await session.flush()


async def prune_calendars(session, tenant, now, limit):
    ids = list(
        await session.scalars(
            select(MeetingDelivery.id)
            .join(Job, Job.id == MeetingDelivery.id)
            .where(
                MeetingDelivery.tenant_id == tenant,
                Job.state.in_(["failed", "succeeded"]),
                Job.updated_at < now - timedelta(days=1),
            )
            .order_by(Job.updated_at, Job.id)
            .limit(limit)
        )
    )
    if ids:
        await session.execute(delete(MeetingDelivery).where(MeetingDelivery.id.in_(ids)))
    used = len(ids)
    if used < limit:
        newer = aliased(CalendarEventVersion)
        rows = list(
            (
                await session.execute(
                    select(
                        CalendarEventVersion.user_id,
                        CalendarEventVersion.id,
                        CalendarEventVersion.revision,
                    )
                    .where(
                        CalendarEventVersion.tenant_id == tenant,
                        CalendarEventVersion.occurred_at < now - timedelta(days=1),
                        select(newer.revision)
                        .where(
                            newer.tenant_id == CalendarEventVersion.tenant_id,
                            newer.user_id == CalendarEventVersion.user_id,
                            newer.id == CalendarEventVersion.id,
                            newer.revision > CalendarEventVersion.revision,
                        )
                        .exists(),
                    )
                    .order_by(
                        CalendarEventVersion.occurred_at,
                        CalendarEventVersion.user_id,
                        CalendarEventVersion.id,
                        CalendarEventVersion.revision,
                    )
                    .limit(limit - used)
                )
            ).all()
        )
        if rows:
            await session.execute(
                delete(CalendarEventVersion).where(
                    CalendarEventVersion.tenant_id == tenant,
                    tuple_(
                        CalendarEventVersion.user_id,
                        CalendarEventVersion.id,
                        CalendarEventVersion.revision,
                    ).in_(rows),
                )
            )
        used += len(rows)
    # Delta stores its bounded baseline in expiring cursors, not history authority.
    return used

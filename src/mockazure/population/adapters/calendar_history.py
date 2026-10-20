"""Append-only calendar history with no invitation delivery or operation facts."""

from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import select

from mockazure.calendar.events import state_for
from mockazure.calendar.lifecycle import OwnerInput
from mockazure.calendar.models import CalendarEvent, CalendarEventVersion, CalendarException
from mockazure.mail.models import Mailbox
from mockazure.mail.repository import resolve_mailbox
from mockazure.mail.send import lock_workflow
from mockazure.population.adapters.arm import json_object
from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.types import FamilyDescriptor, Input, OperationDescriptor


class HistoricalException(Input):
    day: str
    overrides: dict
    deleted: bool = False
    updated_at: datetime


class HistoricalEvent(Input):
    id: str = Field(min_length=1, max_length=128)
    meeting_id: UUID
    organizer_id: UUID
    payload: dict
    exceptions: list[HistoricalException] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid(self):
        # Reuse the existing Graph/schema, recurrence and exception validator without replacement.
        OwnerInput.model_validate(
            {
                "user_id": self.organizer_id,
                "default_group_id": "group",
                "default_calendar_id": "calendar",
                "groups": [{"id": "group", "payload": {"name": "History"}}],
                "calendars": [
                    {"id": "calendar", "group_id": "group", "payload": {"name": "History"}}
                ],
                "events": [
                    {
                        "id": self.id,
                        "calendar_id": "calendar",
                        "meeting_id": self.meeting_id,
                        "organizer_id": self.organizer_id,
                        "payload": self.payload,
                    }
                ],
                "exceptions": [{"master_id": self.id, **e.model_dump()} for e in self.exceptions],
            }
        )
        if "lastModifiedDateTime" not in self.payload:
            raise ValueError("Historical events require lastModifiedDateTime")
        return self


class CalendarHistoryAdapter(PayloadAdapter):
    async def matches_mutation(self, session, ctx, old, mutation, payload):
        if not self.matches(old.properties, payload):
            return False
        value = HistoricalEvent.model_validate(mutation.payload)
        rows = await session.scalars(
            select(CalendarException)
            .where(
                CalendarException.tenant_id == ctx.tenant_id,
                CalendarException.user_id == UUID(mutation.parent),
                CalendarException.master_id == value.id,
            )
            .order_by(CalendarException.day)
        )
        existing = [
            HistoricalException(
                day=r.day, overrides=r.overrides, deleted=r.deleted, updated_at=r.updated_at
            )
            for r in rows
        ]
        return existing == sorted(value.exceptions, key=lambda e: e.day)


def adapter():
    def normalize(payload):
        return HistoricalEvent.model_validate(payload).model_dump(
            mode="json", exclude={"exceptions"}
        )

    async def write(session, ctx, pending):
        await lock_workflow(session, ctx.tenant_id)
        for mutation, payload in pending:
            value = HistoricalEvent.model_validate(mutation.payload)
            if not mutation.parent:
                raise ValueError("Calendar mailbox parent is required")
            box = await resolve_mailbox(session, ctx.tenant_id, mutation.parent)
            if await session.get(Mailbox, (ctx.tenant_id, value.organizer_id)) is None:
                raise ValueError("Organizer mailbox must exist in this tenant")
            state = await state_for(session, box)
            state.revision += 1
            event = CalendarEvent(
                tenant_id=ctx.tenant_id,
                user_id=box.user_id,
                id=value.id,
                calendar_id=state.default_calendar_id,
                meeting_id=value.meeting_id,
                organizer_id=value.organizer_id,
                payload=value.payload,
                revision=state.revision,
            )
            session.add(event)
            await session.flush()
            session.add(
                CalendarEventVersion(
                    tenant_id=ctx.tenant_id,
                    user_id=box.user_id,
                    id=value.id,
                    revision=state.revision,
                    calendar_id=state.default_calendar_id,
                    deleted=False,
                    payload=value.payload,
                    occurred_at=datetime.fromisoformat(
                        value.payload["lastModifiedDateTime"].replace("Z", "+00:00")
                    ),
                )
            )
            for entry in value.exceptions:
                session.add(
                    CalendarException(
                        tenant_id=ctx.tenant_id,
                        user_id=box.user_id,
                        master_id=value.id,
                        **entry.model_dump(),
                    )
                )

    e = CalendarEvent
    descriptor = FamilyDescriptor(
        key="mail.events",
        service="Exchange and mail",
        api_path="/v1.0/users/{user}/events",
        display_fields=["id", "subject", "start", "end"],
        operations=[
            OperationDescriptor(
                key="import",
                mode="fixture",
                method="POST",
                path_template="/_mock/tenants/{tenant}/resources/mail.events/apply",
                api_version="local-fixture-v1",
                input_schema=HistoricalEvent.model_json_schema(),
            )
        ],
        contract_refs=["src/mockazure/providers/graph/schemas/README.md"],
        limitations=["Append-only historical calendar copies; no send, delivery, or native facts."],
    )
    return CalendarHistoryAdapter(
        descriptor,
        ReadSpec(
            e,
            e.id,
            json_object(
                id=e.id,
                meeting_id=e.meeting_id,
                organizer_id=e.organizer_id,
                payload=e.payload,
                subject=e.payload["subject"],
                start=e.payload["start"],
                end=e.payload["end"],
            ),
            e.revision,
            (e.deleted.is_(False),),
            parent=e.user_id,
        ),
        normalize,
        write,
    )

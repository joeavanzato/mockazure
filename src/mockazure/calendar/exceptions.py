"""Occurrence overrides retain identity and travel with durable series delivery."""

import copy
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import delete, select

from mockazure.calendar.contracts import event_input, instant, invalid
from mockazure.calendar.models import CalendarEvent, CalendarException
from mockazure.calendar.recurrence import dates, occurrence_on, recurrence_spec
from mockazure.providers.graph.errors import GraphError


async def overrides_for(session, box, master_id):
    rows = list(
        await session.scalars(
            select(CalendarException).where(
                CalendarException.tenant_id == box.tenant_id,
                CalendarException.user_id == box.user_id,
                CalendarException.master_id == master_id,
            )
        )
    )
    return {row.day: row for row in rows}


def apply_override(value, override):
    if override is None:
        return value
    return {
        **value,
        **copy.deepcopy(override.overrides),
        "type": "exception",
        "lastModifiedDateTime": override.updated_at.isoformat(),
    }


def occurrence_exists(master, day):
    pattern, _, begin, until, count, _ = recurrence_spec(master)
    if day < begin or day > until:
        return False
    for n, current in enumerate(dates(pattern, begin)):
        if (count is not None and n >= count) or current > day:
            return False
        if current == day:
            return True
    return False


async def occurrence_for(session, box, event_id, calendar_id=None, *, deleted=False):
    from mockazure.calendar.events import event_view

    try:
        master_id, encoded = event_id.rsplit(".o.", 1)
        day = datetime.strptime(encoded, "%Y%m%d").date()
    except ValueError:
        raise GraphError(404, "ErrorItemNotFound", "Occurrence was not found") from None
    master = await session.get(CalendarEvent, (box.tenant_id, box.user_id, master_id))
    if (
        not master
        or master.deleted
        or not master.payload.get("recurrence")
        or (calendar_id and master.calendar_id != calendar_id)
    ):
        raise GraphError(404, "ErrorItemNotFound", "Series was not found")
    value = event_view(master)
    if not occurrence_exists(value, day):
        raise GraphError(404, "ErrorItemNotFound", "Occurrence was not found")
    override = await session.get(
        CalendarException, (box.tenant_id, box.user_id, master_id, day.isoformat())
    )
    if override and override.deleted and not deleted:
        raise GraphError(404, "ErrorItemNotFound", "Occurrence was deleted")
    payload = apply_override(occurrence_on(value, day), override)
    return SimpleNamespace(
        id=event_id,
        calendar_id=master.calendar_id,
        payload=payload,
        revision=master.revision,
        organizer_id=master.organizer_id,
        master=master,
        day=day,
        override=override,
    )


async def mutate_occurrence(session, actor, box, event_id, calendar_id, operation, body, now):
    from mockazure.calendar.events import event_fact, save_event
    from mockazure.calendar.meetings import enqueue_meeting

    row = await occurrence_for(session, box, event_id, calendar_id)
    if operation not in {"update", "delete", "cancel"}:
        raise GraphError(
            501, "NotSupported", "Individual occurrence response actions are not in this profile"
        )
    if row.master.organizer_id != box.user_id:
        raise GraphError(403, "ErrorAccessDenied", "Only the organizer can edit this occurrence")
    if "recurrence" in body or "transactionId" in body or "attendees" in body:
        invalid("Change recurrence and attendees on the series master")
    if operation == "update":
        patch = event_input(body, previous=row.payload)
        candidate = {**row.payload, **patch}
        pattern, _, begin, until, count, zone = recurrence_spec(row.master.payload)
        previous = following = None
        for n, current in enumerate(dates(pattern, begin)):
            if current > until or (count is not None and n >= count):
                break
            if current < row.day:
                previous = current
            elif current > row.day:
                following = current
                break
        moved = instant(candidate["start"]).astimezone(zone).date()
        if (previous and moved <= previous) or (following and moved >= following):
            raise GraphError(
                400,
                "ErrorOccurrenceCrossingBoundary",
                "Modified occurrence is crossing adjacent occurrence",
            )
    else:
        if operation == "cancel":
            from mockazure.calendar.contracts import action_input

            action_input("cancel", body)
        elif body:
            invalid("Delete takes no body")
        patch = {}
    override = row.override
    if override is None:
        override = CalendarException(
            tenant_id=box.tenant_id,
            user_id=box.user_id,
            master_id=row.master.id,
            day=row.day.isoformat(),
            overrides={},
            deleted=False,
            updated_at=now,
        )
        session.add(override)
    override.overrides = {**override.overrides, **patch}
    override.deleted = operation in {"delete", "cancel"}
    override.updated_at = now
    await save_event(session, box, row.master, now)
    fact = SimpleNamespace(
        id=row.id,
        calendar_id=row.calendar_id,
        revision=row.master.revision,
        deleted=override.deleted,
    )
    await event_fact(
        session,
        actor,
        box,
        fact,
        "SoftDelete" if override.deleted else "Update",
        {"id": row.id, "parentFolderId": row.calendar_id},
        now,
    )
    if row.master.payload.get("attendees"):
        await enqueue_meeting(
            session,
            actor,
            box,
            row.master,
            "occurrenceCancel" if override.deleted else "occurrenceUpdate",
            body.get("comment", ""),
            now,
        )
    return (
        None
        if override.deleted
        else apply_override(
            occurrence_on(
                {**row.master.payload, "id": row.master.id, "changeKey": str(row.master.revision)},
                row.day,
            ),
            override,
        )
    )


async def snapshot_overrides(session, box, master_id):
    return [
        {
            "day": r.day,
            "overrides": copy.deepcopy(r.overrides),
            "deleted": r.deleted,
            "updated_at": r.updated_at.isoformat(),
        }
        for r in (await overrides_for(session, box, master_id)).values()
    ]


async def restore_overrides(session, box, master_id, values):
    await session.execute(
        delete(CalendarException).where(
            CalendarException.tenant_id == box.tenant_id,
            CalendarException.user_id == box.user_id,
            CalendarException.master_id == master_id,
        )
    )
    for value in values:
        session.add(
            CalendarException(
                tenant_id=box.tenant_id,
                user_id=box.user_id,
                master_id=master_id,
                day=value["day"],
                overrides=value["overrides"],
                deleted=value["deleted"],
                updated_at=datetime.fromisoformat(value["updated_at"]),
            )
        )
    await session.flush()

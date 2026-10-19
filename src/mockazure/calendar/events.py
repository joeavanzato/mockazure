"""Canonical owner-scoped calendars and event mutations in caller transactions."""

import copy
from uuid import UUID, uuid4

from sqlalchemy import select

from mockazure.authorization.evaluator import AuthorizationDenied, OperationRequirement, authorize
from mockazure.calendar.contracts import action_input, event_input, invalid, validate
from mockazure.calendar.models import (
    Calendar,
    CalendarEvent,
    CalendarEventVersion,
    CalendarGroup,
    CalendarState,
)
from mockazure.identity.models import User
from mockazure.mail.authorization import GRAPH
from mockazure.mail.delegation import has_delegation
from mockazure.mail.facts import record_mail_fact
from mockazure.mail.models import MailSettings
from mockazure.mail.repository import resolve_mailbox
from mockazure.mail.send import lock_workflow
from mockazure.providers.graph.body import typed_body
from mockazure.providers.graph.errors import GraphError


def identifier():
    return "AAMk" + uuid4().hex


async def require_calendar_access(session, actor, box, *, write=False):
    if actor.tenant_id != box.tenant_id or actor.audience != GRAPH:
        raise AuthorizationDenied("Current Graph calendar authority is required")
    user = await session.get(User, box.user_id)
    settings = await session.get(MailSettings, box.tenant_id)
    if user is None or user.deleted_at or not settings or not settings.exchange_enabled:
        raise AuthorizationDenied("Calendar mailbox is unavailable")
    permissions = (
        {"Calendars.ReadWrite"}
        if write
        else {"Calendars.ReadBasic.All", "Calendars.Read", "Calendars.ReadWrite"}
    )
    own_delegated = (
        {"Calendars.ReadWrite"}
        if write
        else {"Calendars.ReadBasic", "Calendars.Read", "Calendars.ReadWrite"}
    )
    shared = actor.token_kind == "delegated" and actor.subject_id != box.user_id
    delegated = (
        {"Calendars.ReadWrite.Shared"}
        if write
        else {"Calendars.Read.Shared", "Calendars.ReadWrite.Shared"}
    )
    await authorize(
        actor,
        OperationRequirement(
            "graph",
            frozenset(permissions),
            frozenset(delegated if shared else own_delegated | delegated),
        ),
        session,
    )
    if shared and not await has_delegation(
        session, box.tenant_id, box.user_id, actor.subject_id, "FullAccess"
    ):
        raise AuthorizationDenied("Current shared calendar access is required")


def basic_only(actor):
    return not actor.permissions & {
        "Calendars.Read",
        "Calendars.ReadWrite",
        "Calendars.Read.Shared",
        "Calendars.ReadWrite.Shared",
    }


async def state_for(session, box):
    state = await session.get(CalendarState, (box.tenant_id, box.user_id))
    if state is not None:
        return state
    state = CalendarState(
        tenant_id=box.tenant_id,
        user_id=box.user_id,
        default_group_id=identifier(),
        default_calendar_id=identifier(),
    )
    session.add(state)
    await session.flush()
    session.add(
        CalendarGroup(
            tenant_id=box.tenant_id,
            user_id=box.user_id,
            id=state.default_group_id,
            payload={"name": "My Calendars", "classId": str(uuid4()), "changeKey": "1"},
        )
    )
    await session.flush()
    session.add(
        Calendar(
            tenant_id=box.tenant_id,
            user_id=box.user_id,
            id=state.default_calendar_id,
            group_id=state.default_group_id,
            payload={"name": "Calendar", "color": "auto"},
        )
    )
    await session.flush()
    return state


async def calendar_for(session, box, calendar_id=None, group_id=None):
    state = await state_for(session, box)
    row = await session.get(
        Calendar, (box.tenant_id, box.user_id, calendar_id or state.default_calendar_id)
    )
    if row is None or (group_id and row.group_id != group_id):
        raise GraphError(404, "ErrorItemNotFound", "Calendar was not found")
    return row


async def event_for(session, box, event_id, calendar_id=None, *, deleted=False):
    if ".o." in event_id:
        from mockazure.calendar.exceptions import occurrence_for

        return await occurrence_for(session, box, event_id, calendar_id, deleted=deleted)
    row = await session.get(CalendarEvent, (box.tenant_id, box.user_id, event_id))
    if (
        row is None
        or (row.deleted and not deleted)
        or (calendar_id and row.calendar_id != calendar_id)
    ):
        raise GraphError(404, "ErrorItemNotFound", "Event was not found")
    return row


def calendar_view(row, box, state):
    return {
        **row.payload,
        "id": row.id,
        "owner": {"name": box.primary_smtp, "address": box.primary_smtp},
        "isDefaultCalendar": row.id == state.default_calendar_id,
        "canEdit": True,
        "canShare": False,
        "canViewPrivateItems": True,
        "isRemovable": row.id != state.default_calendar_id,
        "allowedOnlineMeetingProviders": [],
        "defaultOnlineMeetingProvider": "unknown",
    }


def event_view(row, *, basic=False):
    value = copy.deepcopy(row.payload)
    value.update(id=row.id, changeKey=str(row.revision))
    if basic:
        for field in (
            "body",
            "bodyPreview",
            "attachments",
            "extensions",
            "singleValueExtendedProperties",
            "multiValueExtendedProperties",
        ):
            value.pop(field, None)
    return value


async def save_event(session, box, row, now):
    existed = bool(row.revision)
    state = await state_for(session, box)
    state.revision += 1
    row.revision = state.revision
    row.payload = {**row.payload, "lastModifiedDateTime": now.isoformat()}
    await session.flush()
    session.add(
        CalendarEventVersion(
            tenant_id=box.tenant_id,
            user_id=box.user_id,
            id=row.id,
            revision=row.revision,
            calendar_id=row.calendar_id,
            payload=copy.deepcopy(row.payload),
            deleted=row.deleted,
            occurred_at=now,
        )
    )
    await session.flush()
    from mockazure.notifications.publication import publish_calendar

    await publish_calendar(session, box, row, existed, now)


async def event_fact(session, actor, box, row, operation, before, now):
    user = await session.get(User, box.user_id)
    after = (
        {}
        if row.deleted
        else {"id": row.id, "parentFolderId": row.calendar_id, "changeKey": str(row.revision)}
    )
    await record_mail_fact(
        session,
        actor,
        operation,
        {"id": str(box.user_id), "upn": user.upn, "primarySmtpAddress": box.primary_smtp},
        before,
        after,
        now,
    )


async def mutate_event(
    session,
    actor,
    owner_id: UUID,
    calendar_id: str,
    event_id: str | None,
    operation: str,
    body: dict,
    now,
) -> dict | None:
    from mockazure.calendar.meetings import enqueue_meeting

    await lock_workflow(session, actor.tenant_id)
    box = await resolve_mailbox(session, actor.tenant_id, str(owner_id))
    await require_calendar_access(session, actor, box, write=True)
    calendar = await calendar_for(session, box, calendar_id)
    if event_id and ".o." in event_id:
        from mockazure.calendar.exceptions import mutate_occurrence

        return await mutate_occurrence(
            session, actor, box, event_id, calendar.id, operation, body, now
        )
    row = await event_for(session, box, event_id, calendar.id) if event_id else None
    before = (
        {}
        if row is None
        else {"id": row.id, "parentFolderId": row.calendar_id, "changeKey": str(row.revision)}
    )
    prior_attendees = {
        a["emailAddress"]["address"].casefold(): a.get("status")
        for a in (row.payload.get("attendees", []) if row else [])
    }
    if operation == "create":
        body = event_input(body)
        if body.get("transactionId"):
            old = await session.scalar(
                select(CalendarEvent).where(
                    CalendarEvent.tenant_id == actor.tenant_id,
                    CalendarEvent.user_id == owner_id,
                    CalendarEvent.calendar_id == calendar.id,
                    CalendarEvent.transaction_client == actor.client_id,
                    CalendarEvent.transaction_id == body["transactionId"],
                )
            )
            if old:
                if old.deleted:
                    raise GraphError(
                        409, "ErrorInvalidOperation", "The transaction's event was deleted"
                    )
                return event_view(old)
        uid = uuid4()
        defaults = {
            "subject": "",
            "body": {"contentType": "text", "content": ""},
            "attendees": [],
            "categories": [],
            "createdDateTime": now.isoformat(),
            "isOrganizer": True,
            "isCancelled": False,
            "isDraft": False,
            "hasAttachments": False,
            "type": "singleInstance",
            "iCalUId": str(uid),
            "responseStatus": {"response": "organizer", "time": now.isoformat()},
            "organizer": {"emailAddress": {"address": box.primary_smtp}},
            "showAs": "busy",
            "importance": "normal",
            "sensitivity": "normal",
            "responseRequested": True,
            "allowNewTimeProposals": True,
            "isAllDay": False,
            "isReminderOn": True,
            "reminderMinutesBeforeStart": 15,
            "hideAttendees": False,
            "isOnlineMeeting": False,
            "onlineMeetingProvider": "unknown",
            "recurrence": None,
            "originalStartTimeZone": body["start"]["timeZone"],
            "originalEndTimeZone": body["end"]["timeZone"],
        }
        row = CalendarEvent(
            tenant_id=actor.tenant_id,
            user_id=owner_id,
            id=identifier(),
            calendar_id=calendar.id,
            meeting_id=uid,
            organizer_id=owner_id,
            payload=defaults | body,
            revision=0,
            source_revision=0,
            deleted=False,
            transaction_client=actor.client_id if body.get("transactionId") else None,
            transaction_id=body.get("transactionId"),
        )
        session.add(row)
    elif operation == "update":
        if row.organizer_id != owner_id:
            if body.keys() - {"categories", "isReminderOn", "reminderMinutesBeforeStart", "showAs"}:
                invalid("Only the organizer can change meeting content")
        body = event_input(body, previous=row.payload)
        row.payload = {**row.payload, **body}
        if "recurrence" in body and body["recurrence"] is None:
            from mockazure.calendar.exceptions import restore_overrides

            await restore_overrides(session, box, row.id, [])
    elif operation in {"delete", "cancel"}:
        if operation == "cancel":
            action_input(operation, body)
            if row.organizer_id != owner_id:
                invalid("You need to be an organizer to cancel a meeting")
        elif body:
            invalid("Delete takes no body")
        row.deleted = True
        row.payload = {**row.payload, "isCancelled": True}
    else:
        body = action_input(operation, body)
        if row.organizer_id == owner_id:
            invalid("An organizer cannot respond to their own meeting")
        status = {
            "accept": "accepted",
            "tentativelyAccept": "tentativelyAccepted",
            "decline": "declined",
        }[operation]
        row.payload = {
            **row.payload,
            "responseStatus": {"response": status, "time": now.isoformat()},
            "showAs": "busy"
            if operation == "accept"
            else "tentative"
            if operation == "tentativelyAccept"
            else "free",
        }
        if operation == "decline":
            row.deleted = True
    if operation in {"create", "update"}:
        row.payload = {
            **row.payload,
            "attendees": [
                {
                    **a,
                    "status": a.get("status")
                    or prior_attendees.get(a["emailAddress"]["address"].casefold())
                    or {"response": "none", "time": "0001-01-01T00:00:00Z"},
                }
                for a in row.payload.get("attendees", [])
            ],
        }
    if operation in {"create", "update"}:
        row.payload = {
            **row.payload,
            "type": "seriesMaster" if row.payload.get("recurrence") else "singleInstance",
        }
    await save_event(session, box, row, now)
    await event_fact(
        session,
        actor,
        box,
        row,
        "Create" if operation == "create" else "SoftDelete" if row.deleted else "Update",
        before,
        now,
    )
    should_send = (
        row.organizer_id == owner_id
        and (row.payload.get("attendees") or operation in {"update", "delete", "cancel"})
    ) or (
        operation in {"accept", "tentativelyAccept", "decline"} and body.get("sendResponse", True)
    )
    if should_send:
        await enqueue_meeting(session, actor, box, row, operation, body.get("comment", ""), now)
    return None if operation not in {"create", "update"} else event_view(row)


async def mutate_container(
    session, actor, box, kind, operation, body, identifier_value=None, group_id=None
):
    await require_calendar_access(session, actor, box, write=True)
    state = await state_for(session, box)
    cls = CalendarGroup if kind == "groups" else Calendar
    schema = "calendarGroup" if kind == "groups" else "calendar"
    value = typed_body(body, schema)
    allowed = {"name"} if kind == "groups" else {"name", "color", "isDefaultCalendar"}
    if value.keys() - allowed:
        invalid("Calendar property is not writable")
    validate(schema, value)
    if "name" in value and (
        not isinstance(value["name"], str) or not 1 <= len(value["name"]) <= 255
    ):
        invalid("A bounded calendar name is required")
    row = (
        await session.get(cls, (box.tenant_id, box.user_id, identifier_value))
        if identifier_value
        else None
    )
    if operation != "create" and (not row or (group_id and row.group_id != group_id)):
        raise GraphError(404, "ErrorItemNotFound", "Calendar container was not found")
    if operation == "delete":
        if value:
            invalid("Delete takes no body")
        if row.id in {state.default_group_id, state.default_calendar_id}:
            invalid("The default calendar container cannot be deleted")
        if kind == "groups":
            if await session.scalar(
                select(Calendar.id)
                .where(
                    Calendar.tenant_id == box.tenant_id,
                    Calendar.user_id == box.user_id,
                    Calendar.group_id == row.id,
                )
                .limit(1)
            ):
                invalid("Delete calendars before their group")
        else:
            if await session.scalar(
                select(CalendarEvent.id)
                .where(
                    CalendarEvent.tenant_id == box.tenant_id,
                    CalendarEvent.user_id == box.user_id,
                    CalendarEvent.calendar_id == row.id,
                    CalendarEvent.deleted.is_(False),
                )
                .limit(1)
            ):
                invalid("Delete active events before their calendar")
        await session.delete(row)
        return None
    if operation == "create":
        if "name" not in value:
            invalid("A name is required")
        row = cls(tenant_id=box.tenant_id, user_id=box.user_id, id=identifier(), payload={})
        if kind == "calendars":
            group_id = group_id or state.default_group_id
            if await session.get(CalendarGroup, (box.tenant_id, box.user_id, group_id)) is None:
                raise GraphError(404, "ErrorItemNotFound", "Calendar group was not found")
            row.group_id = group_id
            row.payload = {"color": "auto"}
        else:
            row.payload = {"classId": str(uuid4())}
        session.add(row)
    make_default = value.pop("isDefaultCalendar", None)
    if make_default is False and row.id == state.default_calendar_id:
        invalid("Choose another default calendar first")
    if make_default is True:
        state.default_calendar_id = row.id
    row.payload = (
        {**row.payload, **value, "changeKey": str(uuid4())}
        if kind == "groups"
        else {**row.payload, **value}
    )
    await session.flush()
    return {**row.payload, "id": row.id} if kind == "groups" else calendar_view(row, box, state)

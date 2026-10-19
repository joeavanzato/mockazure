"""Bounded calendar windows and current-authority availability on canonical events."""

import json
import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

from sqlalchemy import select

from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.calendar.contracts import instant, invalid
from mockazure.calendar.events import (
    basic_only,
    calendar_for,
    event_view,
    require_calendar_access,
    state_for,
)
from mockazure.calendar.exceptions import apply_override, occurrence_exists, overrides_for
from mockazure.calendar.models import CalendarEvent
from mockazure.calendar.recurrence import expand_occurrences, occurrence_on
from mockazure.mail.message_queries import issue_page, resolve_page
from mockazure.mail.messages import state_for as mail_state
from mockazure.mail.repository import resolve_mailbox
from mockazure.providers.graph.errors import GraphError

MAX_ROWS = 10000
MAX_BYTES = 16 * 1024 * 1024


def window(params):
    try:
        start = datetime.fromisoformat(params["startDateTime"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(params["endDateTime"].replace("Z", "+00:00"))
        start = start.replace(tzinfo=UTC) if start.tzinfo is None else start
        end = end.replace(tzinfo=UTC) if end.tzinfo is None else end
        if end <= start or end - start > timedelta(days=366):
            raise ValueError()
        return start, end
    except (KeyError, ValueError, TypeError):
        invalid("A valid date window of at most366days is required")


def bounded(rows):
    if len(rows) > MAX_ROWS or len(json.dumps(rows, separators=(",", ":")).encode()) > MAX_BYTES:
        raise GraphError(413, "ErrorResponseTooLarge", "Calendar snapshot exceeds local limits")
    return rows


async def window_events(session, box, calendar_id, start, end, series_id=None):
    stmt = select(CalendarEvent).where(
        CalendarEvent.tenant_id == box.tenant_id,
        CalendarEvent.user_id == box.user_id,
        CalendarEvent.calendar_id == calendar_id,
        CalendarEvent.deleted.is_(False),
    )
    if series_id:
        stmt = stmt.where(CalendarEvent.id == series_id)
    masters = list(await session.scalars(stmt.order_by(CalendarEvent.id).limit(MAX_ROWS + 1)))
    if len(masters) > MAX_ROWS:
        raise GraphError(413, "ErrorResponseTooLarge", "Too many calendar events")
    if series_id and not masters:
        raise GraphError(404, "ErrorItemNotFound", "Series was not found")
    output = []
    for row in masters:
        value = event_view(row)
        if value.get("recurrence"):
            overrides = await overrides_for(session, box, row.id)
            values = {v["id"]: v for v in expand_occurrences(value, start, end, MAX_ROWS)}
            # Exceptions may enter the requested window even if their base time did not.
            for key, override in overrides.items():
                day = datetime.fromisoformat(key).date()
                if not occurrence_exists(value, day):
                    continue
                base = occurrence_on(value, day)
                values.pop(base["id"], None)
                changed = apply_override(base, override)
                if (
                    not override.deleted
                    and instant(changed["start"]) < end
                    and instant(changed["end"]) > start
                ):
                    values[base["id"]] = changed
            output.extend(values.values())
        elif not series_id and instant(value["start"]) < end and instant(value["end"]) > start:
            output.append(value)
        bounded(output)
    return sorted(output, key=lambda v: (instant(v["start"]), v["id"]))


def present(value, prefer, basic):
    from mockazure.providers.graph.calendar_routes import formatted_event

    if basic:
        value = event_view(
            SimpleNamespace(payload=value, id=value["id"], revision=value.get("changeKey", "1")),
            basic=True,
        )
    return formatted_event(value, SimpleNamespace(headers={"prefer": prefer}), basic)


async def binding_for(session, actor, owner_id, query):
    box = await resolve_mailbox(session, actor.tenant_id, str(owner_id))
    await require_calendar_access(session, actor, box)
    state = await state_for(session, box)
    calendar = await calendar_for(session, box, query.get("calendar_id"), query.get("group_id"))
    if query.get("series_id"):
        from mockazure.calendar.events import event_for

        master = await event_for(
            session,
            box,
            query["series_id"],
            calendar.id if query.get("default_scoped") else query.get("calendar_id"),
        )
        calendar = await calendar_for(session, box, master.calendar_id)
    binding = {
        "tenant": str(actor.tenant_id),
        "owner": str(box.user_id),
        "client": str(actor.client_id),
        "subject": str(actor.subject_id),
        "permissions": sorted(actor.permissions),
        "path": query["path"],
        "generation": str(state.generation),
        "prefer": query.get("prefer", ""),
    }
    await mail_state(session, box, create=True)
    return box, calendar, binding


async def read_calendar_view(session, actor, owner_id, query, now):
    from mockazure.providers.graph.calendar_routes import options, project

    box, calendar, binding = await binding_for(session, actor, owner_id, query)
    params = query["params"]
    if "$skiptoken" in params:
        if set(params) != {"$skiptoken"}:
            invalid("Do not modify calendar continuation URLs")
        page = await resolve_page(session, params["$skiptoken"], binding, now)
        if page.get("kind") != "calendarView":
            invalid("Wrong calendar continuation kind")
    else:
        allowed = {"startDateTime", "endDateTime", "$top", "$select", "$count"}
        if params.keys() - allowed:
            invalid("Unsupported calendarView query option")
        start, end = window(params)
        from starlette.datastructures import QueryParams

        options_request = SimpleNamespace(
            query_params=QueryParams({k: v for k, v in params.items() if k.startswith("$")})
        )
        opts = options(options_request, "events", False)
        if basic_only(actor) and set(opts["select"] or []) & {
            "body",
            "bodyPreview",
            "attachments",
            "extensions",
            "singleValueExtendedProperties",
            "multiValueExtendedProperties",
        }:
            raise GraphError(403, "ErrorAccessDenied", "Basic calendar read excludes content")
        raw = await window_events(session, box, calendar.id, start, end, query.get("series_id"))
        rows = bounded(
            [
                project(present(v, query.get("prefer", ""), basic_only(actor)), opts["select"])
                for v in raw
            ]
        )
        page = {
            "kind": "calendarView",
            "binding": binding,
            "rows": rows,
            "offset": 0,
            "top": opts["top"],
            "count": opts["count"],
        }
    begin = page["offset"]
    end = begin + page["top"]
    result = {
        "@odata.context": query["base"] + "/v1.0/$metadata#Collection(event)",
        "value": page["rows"][begin:end],
    }
    if page["count"]:
        result["@odata.count"] = len(page["rows"])
    if end < len(page["rows"]):
        page["offset"] = end
        token = await issue_page(
            session, actor.tenant_id, box.user_id, page, now, params.get("$skiptoken")
        )
        result["@odata.nextLink"] = (
            query["base"] + query["path"] + "?" + urlencode({"$skiptoken": token})
        )
    return result


async def get_schedule(session, actor, owner_id, body, prefer, now):
    from mockazure.providers.graph.body import action_parameters

    body = action_parameters(
        body, {"schedules", "startTime", "endTime", "availabilityViewInterval"}
    )
    schedules = body.get("schedules")
    if (
        not isinstance(schedules, list)
        or not 1 <= len(schedules) <= 20
        or any(not isinstance(s, str) or len(s) > 320 for s in schedules)
    ):
        invalid("Provide1–20 bounded schedule addresses")
    interval = body.get("availabilityViewInterval", 30)
    if type(interval) is not int or not 5 <= interval <= 1440:
        invalid("availabilityViewInterval must be5–1440minutes")
    start, end = instant(body.get("startTime")), instant(body.get("endTime"))
    if end <= start or end - start >= timedelta(days=62):
        invalid("getSchedule window must be less than62days")
    owner = await resolve_mailbox(session, actor.tenant_id, str(owner_id))
    await require_calendar_access(session, actor, owner)
    output = []
    for address in schedules:
        try:
            box = await resolve_mailbox(session, actor.tenant_id, address)
            await require_calendar_access(session, actor, box)
            calendar = await calendar_for(session, box)
            rows = await window_events(session, box, calendar.id, start, end)
        except (AuthorizationDenied, GraphError) as error:
            if isinstance(error, GraphError) and error.status not in {403, 404}:
                raise
            output.append(
                {
                    "scheduleId": address,
                    "error": {
                        "responseCode": "ErrorAccessDenied",
                        "message": "Schedule is unavailable or access is not permitted",
                    },
                }
            )
            continue
        slots = [0] * math.ceil((end - start).total_seconds() / (60 * interval))
        items = []
        for row in rows:
            if row.get("isCancelled"):
                continue
            status = row.get("showAs") or "unknown"
            code = {
                "free": 0,
                "workingElsewhere": 0,
                "tentative": 1,
                "busy": 2,
                "oof": 3,
                "unknown": 0,
            }[status]
            for i in range(len(slots)):
                lo = start + timedelta(minutes=i * interval)
                if (
                    instant(row["start"]) < min(end, lo + timedelta(minutes=interval))
                    and instant(row["end"]) > lo
                ):
                    slots[i] = max(slots[i], code)
            value = present(row, prefer, basic_only(actor))
            item = {
                "start": value["start"],
                "end": value["end"],
                "status": status,
                "isPrivate": row.get("sensitivity") == "private",
            }
            if not basic_only(actor) and not item["isPrivate"]:
                item.update(
                    subject=row.get("subject", ""),
                    location=(row.get("location") or {}).get("displayName", ""),
                )
            items.append(item)
        output.append(
            {
                "scheduleId": address,
                "availabilityView": "".join(map(str, slots)),
                "scheduleItems": items,
            }
        )
    return {"value": output}

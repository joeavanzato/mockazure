"""Writable calendar/action contracts backed by the pinned Graph v1.0 CSDL."""

import copy
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mockazure.mail.workflow_contracts import recipient_address
from mockazure.providers.graph.body import typed_body
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.schema_reference import validate_payload

EVENT_FIELDS = frozenset(
    {
        "subject",
        "body",
        "start",
        "end",
        "attendees",
        "categories",
        "importance",
        "sensitivity",
        "showAs",
        "isAllDay",
        "isReminderOn",
        "reminderMinutesBeforeStart",
        "responseRequested",
        "allowNewTimeProposals",
        "hideAttendees",
        "location",
        "locations",
        "recurrence",
        "transactionId",
        "isOnlineMeeting",
        "onlineMeetingProvider",
    }
)


def invalid(message="Invalid calendar request", code="Request_BadRequest"):
    raise GraphError(400, code, message)


def validate(kind, value):
    try:
        validate_payload("v1.0", kind, value, partial=True)
    except ValueError:
        invalid("Body does not match the Graph v1.0 schema")


def instant(value):
    try:
        value = typed_body(value, "dateTimeTimeZone")
        if set(value) != {"dateTime", "timeZone"}:
            raise ValueError()
        zone = UTC if value["timeZone"] in {"UTC", "Etc/UTC"} else ZoneInfo(value["timeZone"])
        local = datetime.fromisoformat(value["dateTime"])
        if local.tzinfo is not None:
            raise ValueError()
        aware = local.replace(tzinfo=zone)
        if aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != local:
            raise ValueError()
        return aware.astimezone(UTC)
    except (KeyError, ValueError, TypeError, ZoneInfoNotFoundError):
        invalid("A valid local dateTime and supported timeZone are required")


def event_input(body, *, previous=None):
    value = typed_body(copy.deepcopy(body), "event")
    if value.keys() - EVENT_FIELDS or (previous is not None and "transactionId" in value):
        invalid("Event property is not writable")
    if isinstance(value.get("body"), dict) and isinstance(value["body"].get("contentType"), str):
        value["body"]["contentType"] = value["body"]["contentType"].lower()
    validate("event", value)
    if value.get("recurrence") is not None:
        raise GraphError(501, "NotSupported", "Recurring event writes are not yet enabled")
    if value.get("isOnlineMeeting") or value.get("onlineMeetingProvider", "unknown") != "unknown":
        raise GraphError(501, "NotSupported", "Online meeting provisioning is not supported")
    if "subject" in value and (
        not isinstance(value["subject"], str) or any(c in value["subject"] for c in "\r\n")
    ):
        invalid("Event subject must be a single-line string")
    if "transactionId" in value and (
        not isinstance(value["transactionId"], str) or not 1 <= len(value["transactionId"]) <= 256
    ):
        invalid("transactionId must be a bounded nonempty string")
    if "attendees" in value:
        if not isinstance(value["attendees"], list) or len(value["attendees"]) > 500:
            invalid("The local attendee limit is500")
        seen = set()
        for attendee in value["attendees"]:
            if set(attendee) - {"@odata.type", "emailAddress", "type"}:
                invalid("Attendee status is read-only")
            address = recipient_address(attendee)
            if address in seen:
                invalid("Duplicate attendee")
            seen.add(address)
    result = {**(previous or {}), **value}
    if "start" not in result or "end" not in result:
        invalid("Event start and end are required")
    start, end = instant(result["start"]), instant(result["end"])
    if end <= start:
        invalid("Event end must follow start")
    if result.get("isAllDay"):
        if result["start"]["timeZone"] != result["end"]["timeZone"] or any(
            datetime.fromisoformat(result[field]["dateTime"]).time().isoformat() != "00:00:00"
            for field in ("start", "end")
        ):
            invalid("All-day events require midnight boundaries in the same time zone")
    if "location" in value:
        value["locations"] = [] if value["location"] is None else [value["location"]]
    elif "locations" in value:
        value["location"] = next(iter(value["locations"] or []), {})
    return value


def action_input(action, body):
    allowed = {"comment"} | (set() if action == "cancel" else {"sendResponse"})
    if not isinstance(body, dict) or body.keys() - allowed:
        invalid("Unknown meeting action parameter")
    if "comment" in body and (not isinstance(body["comment"], str) or len(body["comment"]) > 16384):
        invalid("comment must be a bounded string")
    if "sendResponse" in body and type(body["sendResponse"]) is not bool:
        invalid("sendResponse must be boolean")
    return body

import re
from datetime import datetime, timedelta

from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.schema_reference import validate_payload


def invalid(message="Value is outside the supported tenant-scope PIM profile"):
    raise GraphError(400, "Request_BadRequest", message)


def duration(value):
    if not isinstance(value, str):
        invalid("An ISO 8601 duration is required")
    match = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value)
    if not match or not any(match.groups()) or len(value) > 32:
        invalid("A positive day/hour/minute/second duration is required")
    try:
        result = timedelta(
            days=int(match[1] or 0),
            hours=int(match[2] or 0),
            minutes=int(match[3] or 0),
            seconds=int(match[4] or 0),
        )
    except OverflowError:
        invalid()
    if result <= timedelta(0) or result > timedelta(days=366):
        invalid("Duration exceeds this profile's 366-day bound")
    return result


def timestamp(value):
    try:
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        invalid("Timestamp requires an explicit UTC offset")


def schedule_bounds(value, now, *, maximum, expiration_required):
    if (
        not isinstance(value, dict)
        or set(value) - {"startDateTime", "expiration"}
        or not isinstance(value.get("expiration"), dict)
    ):
        invalid("Nonrecurring scheduleInfo with expiration is required")
    start = timestamp(value["startDateTime"]) if value.get("startDateTime") else now
    # Allow request transit/clock precision, never backdate operational authority.
    if start < now - timedelta(minutes=5) or start > now + timedelta(days=366):
        invalid("Start is outside the supported scheduling window")
    start = max(start, now)
    exp = value["expiration"]
    kind = exp.get("type")
    if kind == "noExpiration" and set(exp) == {"type"}:
        if expiration_required:
            invalid("Policy requires expiration")
        return start, None
    if kind == "afterDuration" and set(exp) == {"type", "duration"}:
        end = start + duration(exp["duration"])
    elif kind == "afterDateTime" and set(exp) == {"type", "endDateTime"}:
        end = timestamp(exp["endDateTime"])
    else:
        invalid("Unsupported expiration shape")
    if not start < end <= start + maximum:
        invalid("Requested duration violates role policy")
    return start, end


def typed(name, body):
    try:
        validate_payload("v1.0", name, body, partial=True)
    except ValueError:
        invalid("Properties do not match the Microsoft Graph v1.0 schema")
    return body

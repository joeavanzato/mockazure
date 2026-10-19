"""Graph recurrence forms and stable local occurrence identities, with explicit bounds."""

import copy
from calendar import monthrange
from datetime import date, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from mockazure.calendar.contracts import instant, invalid, validate
from mockazure.calendar.timezones import resolve_zone
from mockazure.providers.graph.body import typed_body
from mockazure.providers.graph.errors import GraphError

DAYS = {
    name: i
    for i, name in enumerate(
        ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    )
}
INDEX = {"first": 0, "second": 1, "third": 2, "fourth": 3, "last": -1}
TYPES = {
    "daily",
    "weekly",
    "absoluteMonthly",
    "relativeMonthly",
    "absoluteYearly",
    "relativeYearly",
}
MAX_STEPS = 100000


def bounded_int(value, lower, upper):
    return type(value) is int and lower <= value <= upper


def recurrence_spec(series):
    recurrence = typed_body(series["recurrence"], "patternedRecurrence")
    validate("patternedRecurrence", recurrence)
    try:
        pattern = typed_body(recurrence["pattern"], "recurrencePattern")
        span = typed_body(recurrence["range"], "recurrenceRange")
        kind = pattern["type"]
        if kind not in TYPES or not bounded_int(pattern.get("interval"), 1, 10000):
            raise ValueError()
        for key, upper in (("dayOfMonth", 31), ("month", 12)):
            if key in pattern and not bounded_int(pattern[key], 1, upper):
                raise ValueError()
        if "daysOfWeek" in pattern and (
            not pattern["daysOfWeek"]
            or any(d not in DAYS for d in pattern["daysOfWeek"])
            or len(set(pattern["daysOfWeek"])) != len(pattern["daysOfWeek"])
        ):
            raise ValueError()
        if (
            pattern.get("firstDayOfWeek", "sunday") not in DAYS
            or pattern.get("index", "first") not in INDEX
        ):
            raise ValueError()
        required = {
            "weekly": {"daysOfWeek"},
            "absoluteMonthly": {"dayOfMonth"},
            "relativeMonthly": {"daysOfWeek"},
            "absoluteYearly": {"dayOfMonth", "month"},
            "relativeYearly": {"daysOfWeek", "month"},
        }
        if required.get(kind, set()) - pattern.keys():
            raise ValueError()
        if span["type"] not in {"noEnd", "endDate", "numbered"}:
            raise ValueError()
        begin = date.fromisoformat(span["startDate"])
        zone = resolve_zone(span.get("recurrenceTimeZone") or series["start"]["timeZone"])
        if instant(series["start"]).astimezone(zone).date() != begin:
            raise ValueError()
        until = date.fromisoformat(span["endDate"]) if span["type"] == "endDate" else date.max
        if until < begin:
            raise ValueError()
        count = span["numberOfOccurrences"] if span["type"] == "numbered" else None
        if count is not None and not bounded_int(count, 1, 10000):
            raise ValueError()
        if instant(series["end"]) <= instant(series["start"]):
            raise ValueError()
        return pattern, span, begin, until, count, zone
    except (KeyError, TypeError, ValueError):
        invalid("Invalid recurrence pattern, range or event start date")


def month_day(year, month, pattern):
    final = monthrange(year, month)[1]
    if pattern["type"].startswith("absolute"):
        # Outlook's monthly/yearly day selection clips to the month's last day.
        return min(pattern["dayOfMonth"], final)
    candidates = []
    for day in pattern["daysOfWeek"]:
        matches = [d for d in range(1, final + 1) if date(year, month, d).weekday() == DAYS[day]]
        candidates.append(matches[INDEX[pattern.get("index", "first")]])
    return min(candidates)


def dates(pattern, begin):
    kind, interval = pattern["type"], pattern["interval"]
    if kind == "daily":
        for step in range(MAX_STEPS):
            try:
                yield begin + timedelta(days=step * interval)
            except OverflowError:
                return
    elif kind == "weekly":
        first = DAYS[pattern.get("firstDayOfWeek", "sunday")]
        anchor = begin - timedelta(days=(begin.weekday() - first) % 7)
        for step in range(MAX_STEPS):
            for offset in sorted((DAYS[d] - first) % 7 for d in pattern["daysOfWeek"]):
                try:
                    value = anchor + timedelta(weeks=step * interval, days=offset)
                except OverflowError:
                    return
                if value >= begin:
                    yield value
    else:
        for step in range(MAX_STEPS):
            if kind.endswith("Monthly"):
                total = begin.year * 12 + begin.month - 1 + step * interval
                year, month = divmod(total, 12)
                month += 1
            else:
                year, month = begin.year + step * interval, pattern["month"]
            if year > 9999:
                return
            value = date(year, month, month_day(year, month, pattern))
            if value >= begin:
                yield value
    raise GraphError(
        413, "ErrorRecurrenceLimit", "Recurrence evaluation exceeds the local work limit"
    )


def occurrence_id(series_id, day):
    return series_id + ".o." + day.strftime("%Y%m%d")


def occurrence_on(series, day):
    _, _, _, _, _, zone = recurrence_spec(series)
    base_start = instant(series["start"]).astimezone(zone).replace(tzinfo=None)
    base_end = instant(series["end"]).astimezone(zone).replace(tzinfo=None)
    start = datetime.combine(day, base_start.time())
    end = start + (base_end - base_start)
    # Translate the recurrence-local wall time back to the event's original zones.
    local_zone = (
        series["recurrence"]["range"].get("recurrenceTimeZone") or series["start"]["timeZone"]
    )
    output = copy.deepcopy(series)
    for key, local in (("start", start), ("end", end)):
        utc = instant({"dateTime": local.isoformat(), "timeZone": local_zone})
        name = series[key]["timeZone"]
        output[key] = {
            "dateTime": utc.astimezone(resolve_zone(name))
            .replace(tzinfo=None)
            .isoformat(timespec="seconds"),
            "timeZone": name,
        }
    output.update(
        id=occurrence_id(series["id"], day),
        seriesMasterId=series["id"],
        type="occurrence",
        recurrence=None,
        originalStart=instant(output["start"]).isoformat(),
        iCalUId=str(
            uuid5(NAMESPACE_URL, series.get("iCalUId", series["id"]) + "/" + day.isoformat())
        ),
    )
    output.pop("transactionId", None)
    return output


def expand_occurrences(series: dict, start: datetime, end: datetime, limit: int) -> list[dict]:
    if start.tzinfo is None or end.tzinfo is None or end <= start or limit < 1:
        invalid("A bounded, increasing calendar window is required")
    pattern, _, begin, until, count, zone = recurrence_spec(series)
    horizon = end.astimezone(zone).date()
    duration = instant(series["end"]) - instant(series["start"])
    earliest = (start - duration - timedelta(days=2)).astimezone(zone).date()
    result = []
    for n, day in enumerate(dates(pattern, begin)):
        if day > until or day > horizon or (count is not None and n >= count):
            break
        if day < earliest:
            continue
        value = occurrence_on(series, day)
        if instant(value["start"]) < end and instant(value["end"]) > start:
            result.append(value)
            if len(result) > limit:
                raise GraphError(
                    413, "ErrorRecurrenceLimit", "Calendar expansion exceeds the local result limit"
                )
    return result

"""Pinned time-zone and actual Graph recurrence forms, including date and DST boundaries."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from mockazure.providers.graph.errors import GraphError


def series(start="2026-01-01T10:00:00", zone="UTC", pattern=None, range_type="numbered", count=3):
    return {
        "id": "master",
        "iCalUId": "shared-uid",
        "type": "seriesMaster",
        "subject": "Review",
        "start": {"dateTime": start, "timeZone": zone},
        "end": {"dateTime": start.replace("T10:", "T11:"), "timeZone": zone},
        "recurrence": {
            "pattern": pattern or {"type": "daily", "interval": 1},
            "range": {
                "type": range_type,
                "startDate": start[:10],
                **({"numberOfOccurrences": count} if range_type == "numbered" else {}),
            },
        },
    }


@pytest.mark.parametrize(
    "pattern,dates",
    [
        ({"type": "daily", "interval": 2}, ["2026-01-01", "2026-01-03", "2026-01-05"]),
        (
            {
                "type": "weekly",
                "interval": 2,
                "daysOfWeek": ["monday", "thursday"],
                "firstDayOfWeek": "monday",
            },
            ["2026-01-01", "2026-01-12", "2026-01-15"],
        ),
        (
            {"type": "absoluteMonthly", "interval": 1, "dayOfMonth": 15},
            ["2026-01-15", "2026-02-15", "2026-03-15"],
        ),
        (
            {
                "type": "relativeMonthly",
                "interval": 1,
                "daysOfWeek": ["thursday", "friday"],
                "index": "second",
            },
            ["2026-01-08", "2026-02-12", "2026-03-12"],
        ),
        (
            {"type": "absoluteYearly", "interval": 1, "dayOfMonth": 15, "month": 4},
            ["2026-04-15", "2027-04-15", "2028-04-15"],
        ),
        (
            {
                "type": "relativeYearly",
                "interval": 1,
                "daysOfWeek": ["wednesday"],
                "index": "last",
                "month": 11,
            },
            ["2026-11-25", "2027-11-24", "2028-11-29"],
        ),
    ],
)
def test_all_documented_recurrence_patterns(pattern, dates):
    from mockazure.calendar.recurrence import expand_occurrences

    result = expand_occurrences(
        series(pattern=pattern),
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2029, 1, 1, tzinfo=UTC),
        10,
    )
    assert [x["start"]["dateTime"][:10] for x in result] == dates
    assert all(x["type"] == "occurrence" and x["seriesMasterId"] == "master" for x in result)
    assert len({x["iCalUId"] for x in result}) == 3


@pytest.mark.parametrize(
    "start,expected", [("2026-03-01T10:00:00", [15, 14, 14]), ("2026-10-25T10:00:00", [14, 15, 15])]
)
def test_windows_iana_dst_and_stable_occurrence_identity(start, expected):
    from mockazure.calendar.contracts import instant
    from mockazure.calendar.recurrence import expand_occurrences

    body = series(
        start, "Eastern Standard Time", {"type": "weekly", "interval": 1, "daysOfWeek": ["sunday"]}
    )
    begin = datetime.fromisoformat(start).replace(tzinfo=UTC)
    end = datetime(2027, 1, 1, tzinfo=UTC)
    windows = expand_occurrences(body, begin, end, 10)
    body["start"]["timeZone"] = body["end"]["timeZone"] = "America/New_York"
    iana = expand_occurrences(body, begin, end, 10)
    assert [instant(x["start"]).hour for x in windows] == expected
    assert [instant(x["start"]) for x in windows] == [instant(x["start"]) for x in iana]
    assert [x["id"] for x in windows] == [x["id"] for x in iana]
    body["start"]["dateTime"] = start.replace("T10:", "T11:")
    body["end"]["dateTime"] = start.replace("T10:", "T12:")
    assert [x["id"] for x in expand_occurrences(body, begin, end, 10)] == [x["id"] for x in windows]


def test_recurrence_ranges_and_explicit_expansion_bounds():
    from mockazure.calendar.recurrence import expand_occurrences

    body = series(range_type="endDate")
    body["recurrence"]["range"]["endDate"] = "2026-01-03"
    start, end = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 10, tzinfo=UTC)
    assert len(expand_occurrences(body, start, end, 10)) == 3
    unlimited = series(range_type="noEnd")
    assert len(expand_occurrences(unlimited, start, end, 10)) == 9
    with pytest.raises(GraphError) as error:
        expand_occurrences(unlimited, start, end, 2)
    assert error.value.status == 413


@pytest.mark.parametrize("date", ["2026-03-08T02:30:00", "2026-11-01T01:30:00"])
def test_ambiguous_or_nonexistent_local_time_is_explicit(date):
    from mockazure.calendar.contracts import instant

    with pytest.raises(GraphError) as error:
        instant({"dateTime": date, "timeZone": "America/New_York"})
    assert error.value.status == 400


@pytest.mark.parametrize(
    "changes", [{"interval": 0}, {"interval": True}, {"daysOfWeek": ["funday"]}, {"dayOfMonth": 32}]
)
def test_invalid_recurrence_values_are_not_silently_ignored(changes):
    from mockazure.calendar.recurrence import expand_occurrences

    body = deepcopy(series())
    body["recurrence"]["pattern"].update(changes)
    with pytest.raises(GraphError):
        expand_occurrences(
            body, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC), 100
        )


@pytest.mark.parametrize("value", ["2026-02-30", "20260101", "2026-01-01T00:00:00Z", 20260101])
def test_graph_date_primitive_requires_real_iso_date(value):
    from mockazure.providers.graph.schema_reference import validate_payload

    with pytest.raises(ValueError):
        validate_payload(
            "v1.0", "recurrenceRange", {"type": "noEnd", "startDate": value}, partial=True
        )


def test_outlook_month_end_clips_instead_of_skipping_short_months():
    from mockazure.calendar.recurrence import expand_occurrences

    body = series(
        "2026-01-31T10:00:00", pattern={"type": "absoluteMonthly", "interval": 1, "dayOfMonth": 31}
    )
    result = expand_occurrences(
        body, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 4, 1, tzinfo=UTC), 10
    )
    assert [x["start"]["dateTime"][:10] for x in result] == [
        "2026-01-31",
        "2026-02-28",
        "2026-03-31",
    ]


def test_recurrence_does_not_expand_irrelevant_historical_dst_gap():
    from mockazure.calendar.recurrence import expand_occurrences

    body = series("2026-03-01T10:00:00", "America/New_York", range_type="noEnd")
    body["start"]["dateTime"] = "2026-03-01T02:30:00"
    body["end"]["dateTime"] = "2026-03-01T03:30:00"
    result = expand_occurrences(
        body, datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 2, tzinfo=UTC), 10
    )
    assert len(result) == 1 and result[0]["start"]["dateTime"] == "2026-10-01T02:30:00"

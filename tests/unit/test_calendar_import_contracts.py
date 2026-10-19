"""Imported calendar state must remain queryable under the public event semantics."""

import copy
from uuid import uuid4

import pytest

from mockazure.calendar.lifecycle import CalendarStateInput


def state():
    owner = str(uuid4())
    return {
        "owners": [
            {
                "user_id": owner,
                "default_group_id": "g",
                "default_calendar_id": "c",
                "groups": [{"id": "g", "payload": {"name": "Calendars"}}],
                "calendars": [{"id": "c", "group_id": "g", "payload": {"name": "Calendar"}}],
                "events": [
                    {
                        "id": "e",
                        "calendar_id": "c",
                        "meeting_id": str(uuid4()),
                        "organizer_id": owner,
                        "payload": {
                            "subject": "Imported",
                            "start": {"dateTime": "2026-10-25T10:00:00", "timeZone": "UTC"},
                            "end": {"dateTime": "2026-10-25T11:00:00", "timeZone": "UTC"},
                        },
                    }
                ],
            }
        ]
    }


def test_calendar_import_rejects_missing_or_reversed_time():
    good = state()
    CalendarStateInput.model_validate(good)
    for change in ("missing", "reversed"):
        bad = copy.deepcopy(good)
        event = bad["owners"][0]["events"][0]["payload"]
        if change == "missing":
            del event["start"]
        else:
            event["end"]["dateTime"] = "2026-10-25T09:00:00"
        with pytest.raises(ValueError):
            CalendarStateInput.model_validate(bad)


@pytest.mark.parametrize("invalid", ["nonoccurrence", "negative_duration"])
def test_calendar_import_rejects_unqueryable_exception(invalid):
    good = state()
    owner = good["owners"][0]
    owner["events"][0]["payload"]["recurrence"] = {
        "pattern": {"type": "daily", "interval": 1},
        "range": {"type": "numbered", "startDate": "2026-10-25", "numberOfOccurrences": 3},
    }
    CalendarStateInput.model_validate(good)
    owner["exceptions"] = [
        {
            "master_id": "e",
            "day": "2026-11-20" if invalid == "nonoccurrence" else "2026-10-26",
            "overrides": {}
            if invalid == "nonoccurrence"
            else {"end": {"dateTime": "2026-10-26T09:00:00", "timeZone": "UTC"}},
            "updated_at": "2026-10-18T00:00:00Z",
        }
    ]
    with pytest.raises(ValueError):
        CalendarStateInput.model_validate(good)


def test_calendar_import_rejects_duplicate_transaction_identity():
    good = state()
    owner = good["owners"][0]
    event = owner["events"][0]
    event.update(transaction_client=str(uuid4()), transaction_id="one-operation")
    CalendarStateInput.model_validate(good)
    owner["events"].append({**copy.deepcopy(event), "id": "another-event"})
    with pytest.raises(ValueError):
        CalendarStateInput.model_validate(good)

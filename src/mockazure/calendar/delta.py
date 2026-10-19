"""Window-bound calendar delta snapshots with current authorization on every round."""

import hashlib
import json
import re
from urllib.parse import urlencode

from mockazure.calendar.contracts import invalid
from mockazure.calendar.events import basic_only
from mockazure.calendar.views import binding_for, bounded, present, window, window_events
from mockazure.mail.message_queries import issue_page, resolve_page


def fingerprint(value):
    stable = {k: v for k, v in value.items() if k not in {"changeKey", "lastModifiedDateTime"}}
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def read_calendar_delta(session, actor, owner_id, query, now):
    box, calendar, binding = await binding_for(session, actor, owner_id, query)
    params = query["params"]
    token = params.get("$skiptoken") or params.get("$deltatoken")
    if token:
        if len(params) != 1:
            invalid("Do not modify calendar delta continuation URLs")
        saved = await resolve_page(session, token, binding, now)
        expected = "calendarDeltaPage" if "$skiptoken" in params else "calendarDelta"
        if saved.get("kind") != expected:
            invalid("Wrong calendar delta token kind")
    else:
        if params.keys() - {"startDateTime", "endDateTime"}:
            invalid("Unsupported calendar delta option")
        window(params)
        match = re.search(r"odata.maxpagesize=(\d+)", query.get("prefer", ""))
        top = int(match[1]) if match else 100
        if not 1 <= top <= 1000:
            invalid("odata.maxpagesize must be1–1000")
        saved = {
            "kind": "calendarDelta",
            "binding": binding,
            "window": params,
            "baseline": {},
            "top": top,
        }
    if saved["kind"] == "calendarDelta":
        start, end = window(saved["window"])
        raw = await window_events(session, box, calendar.id, start, end)
        rows = bounded([present(v, query.get("prefer", ""), basic_only(actor)) for v in raw])
        current = {v["id"]: fingerprint(v) for v in rows}
        changes = [v for v in rows if saved["baseline"].get(v["id"]) != current[v["id"]]]
        changes.extend(
            {"id": identifier, "@removed": {"reason": "deleted"}}
            for identifier in sorted(saved["baseline"].keys() - current.keys())
        )
        saved = {
            **saved,
            "kind": "calendarDeltaPage",
            "rows": changes,
            "baseline": current,
            "offset": 0,
        }
    end = saved["offset"] + saved["top"]
    result = {"value": saved["rows"][saved["offset"] : end]}
    if end < len(saved["rows"]):
        saved["offset"] = end
        key = "$skiptoken"
        link = "@odata.nextLink"
    else:
        saved = {k: v for k, v in saved.items() if k not in {"rows", "offset"}}
        saved["kind"] = "calendarDelta"
        key = "$deltatoken"
        link = "@odata.deltaLink"
    next_token = await issue_page(session, actor.tenant_id, box.user_id, saved, now, token)
    result[link] = query["base"] + query["path"] + "?" + urlencode({key: next_token})
    result["@odata.context"] = query["base"] + "/v1.0/$metadata#Collection(event)"
    return result

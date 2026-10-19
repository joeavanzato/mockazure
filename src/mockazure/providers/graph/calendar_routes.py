"""Graph v1.0 calendar containers, events and meeting response actions."""

import re
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select

from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.calendar.contracts import instant, invalid
from mockazure.calendar.events import (
    basic_only,
    calendar_for,
    calendar_view,
    event_for,
    event_view,
    mutate_container,
    mutate_event,
    require_calendar_access,
    state_for,
)
from mockazure.calendar.models import Calendar, CalendarEvent, CalendarGroup
from mockazure.mail.message_queries import body_format, issue_page, resolve_page
from mockazure.mail.messages import state_for as mail_state
from mockazure.mail.repository import resolve_mailbox
from mockazure.mail.send import lock_workflow
from mockazure.mail.workflow_contracts import references
from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import principal

router = APIRouter()


def options(request, kind, item):
    pairs = list(request.query_params.multi_items())
    query = dict(pairs)
    allowed = {"$select"} if item else {"$select", "$top", "$skiptoken", "$count"}
    if len(query) != len(pairs) or query.keys() - allowed:
        raise GraphError(400, "Request_UnsupportedQuery", "Unsupported calendar query option")
    if "$skiptoken" in query and len(query) > 1:
        invalid("Do not modify the continuation URL")
    fields = query.get("$select")
    if fields:
        fields = fields.split(",")
        schema = {"events": "event", "calendars": "calendar", "groups": "calendarGroup"}[kind]
        if any(field not in references()["types"][schema]["properties"] for field in fields):
            invalid("Unknown selected calendar property")
    top = query.get("$top", "100")
    if not top.isdecimal() or not 1 <= int(top) <= 1000:
        invalid("$top must be1–1000")
    if query.get("$count", "false") not in {"true", "false"}:
        invalid("$count must be boolean")
    return {
        "select": fields,
        "top": int(top),
        "count": query.get("$count") == "true",
        "token": query.get("$skiptoken"),
    }


def formatted_event(row, request, basic):
    value = event_view(row, basic=basic) if not isinstance(row, dict) else dict(row)
    prefer = request.headers.get("prefer", "")
    body = re.search(r'outlook.body-content-type="(text|html)"', prefer, re.I)
    if value.get("body"):
        value["body"] = body_format(value["body"], body.group(1).lower() if body else "html")
    zone = re.search(r'outlook.timezone="([^"]+)"', prefer, re.I)
    zone = zone.group(1) if zone else "UTC"
    from mockazure.calendar.timezones import resolve_zone

    target = resolve_zone(zone)
    for field in ("start", "end"):
        if value.get(field):
            local = instant(value[field]).astimezone(target).replace(tzinfo=None)
            value[field] = {"dateTime": local.isoformat(timespec="seconds"), "timeZone": zone}
    return value


def preference_headers(request):
    accepted = re.findall(
        r'outlook\.(?:timezone="[^"]+"|body-content-type="(?:text|html)")',
        request.headers.get("prefer", ""),
        re.I,
    )
    return {"Preference-Applied": ", ".join(accepted)} if accepted else {}


def project(value, fields):
    return (
        value
        if not fields
        else {k: v for k, v in value.items() if k in fields or k in {"id", "@odata.type"}}
    )


def register(
    path, kind, *, self_path=False, item=False, action=None, default=False, calendar_scoped=False
):
    async def handle(request: Request):
        try:
            async with request.app.state.database.sessions.begin() as session:
                actor = await principal(request, session)
                if self_path and actor.token_kind != "delegated":
                    invalid("/me requires delegated authentication")
                await lock_workflow(session, actor.tenant_id)
                box = await resolve_mailbox(
                    session,
                    actor.tenant_id,
                    str(actor.subject_id) if self_path else request.path_params["user_id"],
                )
                await require_calendar_access(session, actor, box, write=request.method != "GET")
                state = await state_for(session, box)
                now = getattr(request.app.state, "clock", lambda: datetime.now(UTC))()
                group_id = request.path_params.get("group_id")
                calendar_id = request.path_params.get("calendar_id")
                if default or (calendar_scoped and not calendar_id):
                    calendar_id = state.default_calendar_id
                if calendar_id:
                    await calendar_for(session, box, calendar_id, group_id)
                if (
                    group_id
                    and await session.get(CalendarGroup, (box.tenant_id, box.user_id, group_id))
                    is None
                ):
                    raise GraphError(404, "ErrorItemNotFound", "Calendar group was not found")
                if request.method != "GET":
                    if request.query_params:
                        invalid("Mutation query options are unsupported")
                    body = await read_json(
                        request, allow_empty=bool(action) or request.method == "DELETE"
                    )
                    if kind == "events":
                        event_id = request.path_params.get("event_id")
                        if event_id:
                            current = await event_for(session, box, event_id, calendar_id)
                            calendar_id = current.calendar_id
                            if request.headers.get("if-match") not in {
                                None,
                                "*",
                                'W/"' + str(current.revision) + '"',
                            }:
                                raise GraphError(
                                    412, "ErrorPreconditionFailed", "Event version does not match"
                                )
                        operation = (
                            action
                            or {"POST": "create", "PATCH": "update", "DELETE": "delete"}[
                                request.method
                            ]
                        )
                        value = await mutate_event(
                            session, actor, box.user_id, calendar_id, event_id, operation, body, now
                        )
                    else:
                        operation = {"POST": "create", "PATCH": "update", "DELETE": "delete"}[
                            request.method
                        ]
                        value = await mutate_container(
                            session,
                            actor,
                            box,
                            kind,
                            operation,
                            body,
                            group_id
                            if kind == "groups" and item
                            else calendar_id
                            if kind == "calendars" and item
                            else None,
                            group_id if kind == "calendars" else None,
                        )
                    status = (
                        202
                        if action
                        else 204
                        if request.method == "DELETE"
                        else 201
                        if request.method == "POST"
                        else 200
                    )
                    if value is None:
                        return Response(status_code=status)
                    if kind == "events":
                        value = formatted_event(value, request, False)
                    return JSONResponse(
                        value,
                        status_code=status,
                        headers=preference_headers(request) if kind == "events" else {},
                    )
                query = options(request, kind, item)
                basic = basic_only(actor)
                if (
                    kind == "events"
                    and basic
                    and set(query["select"] or [])
                    & {
                        "body",
                        "bodyPreview",
                        "attachments",
                        "extensions",
                        "singleValueExtendedProperties",
                        "multiValueExtendedProperties",
                    }
                ):
                    raise GraphError(
                        403, "ErrorAccessDenied", "Calendar basic read excludes message content"
                    )
                if item:
                    if kind == "events":
                        row = await event_for(
                            session, box, request.path_params["event_id"], calendar_id
                        )
                        value = formatted_event(row, request, basic)
                        headers = {"ETag": 'W/"' + str(row.revision) + '"'}
                    elif kind == "calendars":
                        row = await calendar_for(session, box, calendar_id, group_id)
                        value = calendar_view(row, box, state)
                        headers = {}
                    else:
                        row = await session.get(
                            CalendarGroup, (box.tenant_id, box.user_id, group_id)
                        )
                        value = {**row.payload, "id": row.id}
                        headers = {}
                    return JSONResponse(
                        project(value, query["select"]),
                        headers={
                            **headers,
                            **(preference_headers(request) if kind == "events" else {}),
                        },
                    )
                binding = {
                    "tenant": str(actor.tenant_id),
                    "user": str(box.user_id),
                    "client": str(actor.client_id),
                    "subject": str(actor.subject_id),
                    "permissions": sorted(actor.permissions),
                    "path": request.url.path,
                    "generation": str(state.generation),
                    "prefer": request.headers.get("prefer", ""),
                }
                if query["token"]:
                    page = await resolve_page(session, query["token"], binding, now)
                    rows = page["rows"]
                    offset = page["offset"]
                    query = page["query"]
                else:
                    cls = {"events": CalendarEvent, "calendars": Calendar, "groups": CalendarGroup}[
                        kind
                    ]
                    stmt = select(cls).where(
                        cls.tenant_id == actor.tenant_id, cls.user_id == box.user_id
                    )
                    if kind == "events":
                        stmt = stmt.where(CalendarEvent.deleted.is_(False))
                        if calendar_id:
                            stmt = stmt.where(CalendarEvent.calendar_id == calendar_id)
                    elif kind == "calendars" and group_id:
                        stmt = stmt.where(Calendar.group_id == group_id)
                    raw = list(await session.scalars(stmt.order_by(cls.id).limit(10001)))
                    if len(raw) > 10000:
                        raise GraphError(
                            413,
                            "ErrorResponseTooLarge",
                            "Calendar collection exceeds the local snapshot bound",
                        )
                    rows = [
                        project(
                            formatted_event(r, request, basic)
                            if kind == "events"
                            else calendar_view(r, box, state)
                            if kind == "calendars"
                            else {**r.payload, "id": r.id},
                            query["select"],
                        )
                        for r in raw
                    ]
                    offset = 0
                result = {
                    "@odata.context": str(request.base_url).rstrip("/")
                    + "/v1.0/$metadata#"
                    + ("calendarGroups" if kind == "groups" else kind),
                    "value": rows[offset : offset + query["top"]],
                }
                if query["count"]:
                    result["@odata.count"] = len(rows)
                if offset + query["top"] < len(rows):
                    await mail_state(session, box, create=True)
                    token = await issue_page(
                        session,
                        actor.tenant_id,
                        box.user_id,
                        {
                            "binding": binding,
                            "rows": rows,
                            "offset": offset + query["top"],
                            "query": query,
                        },
                        now,
                    )
                    result["@odata.nextLink"] = (
                        str(request.url).split("?")[0] + "?" + urlencode({"$skiptoken": token})
                    )
                return JSONResponse(
                    result, headers=preference_headers(request) if kind == "events" else {}
                )
        except AuthorizationDenied:
            raise GraphError(
                403, "ErrorAccessDenied", "Calendar operation is not permitted"
            ) from None

    methods = ["POST"] if action else ["GET", "PATCH", "DELETE"] if item else ["GET", "POST"]
    router.add_api_route("/v1.0" + path, handle, methods=methods)
    if action:
        parent, name = path.rsplit("/", 1)
        router.add_api_route(
            "/v1.0" + parent + "/microsoft.graph." + name, handle, methods=methods
        )


for self_path in (False, True):
    prefix = "/me" if self_path else "/users/{user_id}"
    register(prefix + "/calendarGroups", "groups", self_path=self_path)
    register(prefix + "/calendarGroups/{group_id}", "groups", self_path=self_path, item=True)
    register(prefix + "/calendar", "calendars", self_path=self_path, item=True, default=True)
    for base in (prefix + "/calendars", prefix + "/calendarGroups/{group_id}/calendars"):
        register(base, "calendars", self_path=self_path)
        register(base + "/{calendar_id}", "calendars", self_path=self_path, item=True)
    for parent in (
        prefix,
        prefix + "/calendar",
        prefix + "/calendars/{calendar_id}",
        prefix + "/calendarGroups/{group_id}/calendars/{calendar_id}",
    ):
        scoped = parent != prefix
        register(parent + "/events", "events", self_path=self_path, calendar_scoped=scoped)
        register(
            parent + "/events/{event_id}",
            "events",
            self_path=self_path,
            item=True,
            calendar_scoped=scoped,
        )
        for action in ("accept", "tentativelyAccept", "decline", "cancel"):
            register(
                parent + "/events/{event_id}/" + action,
                "events",
                self_path=self_path,
                action=action,
                calendar_scoped=scoped,
            )

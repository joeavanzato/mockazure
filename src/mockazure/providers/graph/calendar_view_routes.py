"""Documented v1.0 calendarView, instances, availability and primary-calendar delta."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.calendar.contracts import invalid
from mockazure.calendar.delta import read_calendar_delta
from mockazure.calendar.views import get_schedule, read_calendar_view
from mockazure.mail.send import lock_workflow
from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import principal

router = APIRouter()


def register(path, kind, self_path=False, default_scoped=False):
    async def handle(request: Request):
        from mockazure.providers.graph.calendar_routes import preference_headers

        try:
            async with request.app.state.database.sessions.begin() as session:
                actor = await principal(request, session)
                if self_path and actor.token_kind != "delegated":
                    invalid("/me requires delegated authentication")
                await lock_workflow(session, actor.tenant_id)
                owner = actor.subject_id if self_path else request.path_params["user_id"]
                now = getattr(request.app.state, "clock", lambda: datetime.now(UTC))()
                params = dict(request.query_params)
                if len(params) != len(list(request.query_params.multi_items())):
                    invalid("Duplicate calendar query option")
                query = {
                    "params": params,
                    "path": request.url.path,
                    "base": str(request.base_url).rstrip("/"),
                    "prefer": request.headers.get("prefer", ""),
                    "calendar_id": request.path_params.get("calendar_id"),
                    "series_id": request.path_params.get("event_id"),
                    "group_id": request.path_params.get("group_id"),
                    "default_scoped": default_scoped,
                }
                if kind == "schedule":
                    if params:
                        invalid("getSchedule does not accept query options")
                    result = await get_schedule(
                        session, actor, owner, await read_json(request), query["prefer"], now
                    )
                else:
                    reader = read_calendar_delta if kind == "delta" else read_calendar_view
                    result = await reader(session, actor, owner, query, now)
                return JSONResponse(result, headers=preference_headers(request))
        except AuthorizationDenied:
            raise GraphError(403, "ErrorAccessDenied", "Calendar access is not permitted") from None

    router.add_api_route(
        "/v1.0" + path, handle, methods=["POST"] if kind == "schedule" else ["GET"]
    )


for self_path in (False, True):
    prefix = "/me" if self_path else "/users/{user_id}"
    register(prefix + "/calendarView/delta", "delta", self_path)
    register(prefix + "/calendar/getSchedule", "schedule", self_path)
    for parent in (
        prefix,
        prefix + "/calendar",
        prefix + "/calendars/{calendar_id}",
        prefix + "/calendarGroups/{group_id}/calendars/{calendar_id}",
    ):
        register(parent + "/calendarView", "view", self_path)
        register(
            parent + "/events/{event_id}/instances",
            "instances",
            self_path,
            default_scoped=parent.endswith("/calendar"),
        )

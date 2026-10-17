"""Activity Logs share immutable evidence snapshots and enforce current scoped ARM grants."""

import hashlib
import re
from datetime import UTC, datetime
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import func, literal_column, or_, select, text, tuple_

from mockazure.evidence.models import EvidenceCursor
from mockazure.evidence.models import EvidenceVersion as V
from mockazure.evidence.repository import lock_state
from mockazure.providers.arm.activity import datetime_ticks
from mockazure.providers.arm.activity_query import compile_activity_filter, selected_fields
from mockazure.providers.arm.authorization import grant_query
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import resolve_scope
from mockazure.providers.arm.inventory_routes import request_scope
from mockazure.providers.arm.models import RoleAssignment as R
from mockazure.providers.arm.probes import principal
from mockazure.providers.arm.query import invalid
from mockazure.providers.graph.evidence.paging import (
    EvidenceSnapshot,
    issue_cursor,
    snapshot_evidence,
)

router = APIRouter()
VERSION = "2015-04-01"
ACTION = "Microsoft.Insights/eventtypes/values/read"


def bad_cursor():
    raise ArmError(400, "InvalidSkipToken", "Activity Logs continuation is unavailable or changed")


def query_parameters(request):
    items = request.query_params.multi_items()
    args = dict(items)
    if args.get("api-version") != VERSION:
        raise ArmError(400, "InvalidApiVersionParameter", "Unsupported Activity Logs API version")
    if len(items) != len(args) or set(args) - {"api-version", "$filter", "$select", "$skiptoken"}:
        invalid("Duplicate or unsupported Activity Logs parameters")
    if sum(len(k.encode()) + len(v.encode()) for k, v in items) > 16384:
        invalid("Activity Logs query exceeds 16 KiB")
    if "$skiptoken" in args:
        if set(args) != {"api-version", "$skiptoken"} or not args["$skiptoken"]:
            bad_cursor()
        return None, args["$skiptoken"]
    compile_activity_filter(args.get("$filter"))
    selected_fields(args.get("$select"))
    return {"filter": args["$filter"], "select": args.get("$select")}, None


async def read_activity(session, actor, subscription, query, cursor, now, *, path, origin):
    scope = request_scope(str(subscription))
    await resolve_scope(session, actor.tenant_id, scope)
    grants = await grant_query(session, actor, ACTION)
    grants = grants.where(R.subscription_id == scope.subscription_id)
    if await session.scalar(grants.limit(1)) is None:
        raise ArmError(403, "AuthorizationFailed", "No readable Activity Logs scopes")
    current = await lock_state(session, actor.tenant_id)
    binding = {
        "service": "arm-activity",
        "tenant": str(actor.tenant_id),
        "client": str(actor.client_id),
        "subject": str(actor.subject_id),
        "kind": actor.token_kind,
        "permissions": sorted(actor.permissions),
        "path": path.lower(),
        "version": VERSION,
        "generation": str(current.generation),
    }
    last = None
    if cursor:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", cursor):
            bad_cursor()
        row = await session.get(EvidenceCursor, hashlib.sha256(cursor.encode()).hexdigest())
        if (
            row is None
            or row.tenant_id != actor.tenant_id
            or row.expires_at <= now
            or row.generation != current.generation
            or row.state.get("binding") != binding
        ):
            bad_cursor()
        snapshot = EvidenceSnapshot(
            row.generation, row.revision, row.evidence_time, row.retention_cutoff, row.expires_at
        )
        query, last = row.state["query"], row.state["last"]
    else:
        snapshot = await snapshot_evidence(session, actor.tenant_id, now)
    visible = (
        grants.where(
            or_(
                R.scope_key == V.arm_scope_key,
                func.left(V.arm_scope_key, func.length(R.scope_key) + 1) == R.scope_key + "/",
            )
        )
        .correlate(V)
        .exists()
    )
    statement = select(V.record_id, V.arm_event_ticks, V.payload).where(
        V.tenant_id == actor.tenant_id,
        V.arm_subscription_id == scope.subscription_id,
        V.family == literal_column("'activityLog'"),
        V.generation == snapshot.generation,
        V.valid_from <= snapshot.revision,
        or_(V.valid_to.is_(None), V.valid_to > snapshot.revision),
        V.arm_event_ticks >= datetime_ticks(snapshot.retention_cutoff),
        V.arm_event_ticks <= datetime_ticks(snapshot.evidence_time),
        V.arm_available_ticks <= datetime_ticks(snapshot.evidence_time),
        compile_activity_filter(query["filter"]),
        visible,
    )
    if last:
        statement = statement.where(
            tuple_(V.arm_event_ticks, V.record_id) > tuple_(int(last[0]), UUID(last[1]))
        )
    rows = (
        await session.execute(statement.order_by(V.arm_event_ticks, V.record_id).limit(101))
    ).all()
    selected = selected_fields(query["select"])
    body = {
        "value": [
            {k: v for k, v in row.payload.items() if selected is None or k in selected}
            for row in rows[:100]
        ]
    }
    if len(rows) > 100:
        last_row = rows[99]
        state = {
            "binding": binding,
            "query": query,
            "last": [last_row.arm_event_ticks, str(last_row.record_id)],
        }
        token = await issue_cursor(session, snapshot, actor.tenant_id, state, cursor)
        body["nextLink"] = (
            origin + path + "?" + urlencode({"api-version": VERSION, "$skiptoken": token})
        )
    return body


@router.get(
    "/subscriptions/{subscription}/providers/microsoft.insights/eventtypes/management/values"
)
async def activity_logs(subscription: str, request: Request):
    query, token = query_parameters(request)
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        profile = request.app.state.settings.advertised_endpoints(request.headers.get("host", ""))
        result = await read_activity(
            session,
            actor,
            subscription,
            query,
            token,
            datetime.now(UTC),
            path=request.url.path,
            origin=profile.arm,
        )
    # Commit the authorized snapshot first; the one-shot fault must survive its
    # own 429 response and must never mask authorization failures.
    async with request.app.state.database.sessions.begin() as session:
        retry = await session.scalar(
            text("""
DELETE FROM arm_read_faults
WHERE tenant_id=:tenant AND subscription_id=:subscription AND client_id=:client
AND (kind='next_read_429' OR (kind='next_page_429' AND :continuation))
RETURNING retry_after
"""),
            dict(
                tenant=actor.tenant_id,
                subscription=UUID(subscription),
                client=actor.client_id,
                continuation=token is not None,
            ),
        )
    if retry is not None:
        error = ArmError(429, "TooManyRequests", "ARM request limit reached; retry later")
        error.headers["Retry-After"] = str(retry)
        raise error
    return result

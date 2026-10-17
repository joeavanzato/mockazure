"""Opaque immutable UAL record pages and permission-filtered query listings."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, tuple_

from mockazure.auditlog.authorization import authorize_query
from mockazure.auditlog.models import AuditQuery, AuditQueryItem
from mockazure.auditlog.queries import get_query, query_view
from mockazure.db.job_model import Job
from mockazure.providers.graph.continuations import (
    CursorState,
    binding_for,
    issue_cursor,
    resolve_cursor,
    state_link,
)
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import context_url
from mockazure.providers.graph.query.ast import QuerySpec
from mockazure.providers.graph.schema_reference import schema


def options(pairs, record):
    if len(dict(pairs)) != len(pairs):
        raise GraphError(400, "Request_UnsupportedQuery", "Duplicate query option")
    values = dict(pairs)
    if values.keys() - {"$top", "$select"}:
        raise GraphError(400, "Request_UnsupportedQuery", "Unsupported audit query option")
    top = values.get("$top", "100")
    if not top.isascii() or not top.isdigit() or not 1 <= int(top) <= 1000:
        raise GraphError(400, "BadRequest", "$top must be 1..1000")
    fields = tuple(values["$select"].split(",")) if "$select" in values else None
    kind = "security.auditLogRecord" if record else "security.auditLogQuery"
    if fields is not None and (
        not fields or not set(fields) <= schema("v1.0")["types"][kind]["properties"].keys()
    ):
        raise GraphError(400, "Request_UnsupportedQuery", "Unsupported selected audit property")
    return QuerySpec(top=min(int(top), 100), select=fields)


def selected(value, fields):
    return {
        key: item for key, item in value.items() if fields is None or key in fields or key == "id"
    }


async def read_page(session, actor, request, version, now, identifier=None):
    record = identifier is not None
    row = None
    if record:
        row, job = await get_query(session, actor, identifier, now, "records")
        if job.state != "succeeded":
            raise GraphError(409, "AuditQueryNotReady", "Audit query has not succeeded")
        resource = f"security/auditLog/queries/{row.id}/records"
    else:
        allowed = await authorize_query(session, actor, "list")
        resource = "security/auditLog/queries"
    binding = await binding_for(session, actor, version, resource)
    pairs = list(request.query_params.multi_items())
    token = cursor = None
    if any(key == "$skiptoken" for key, _ in pairs):
        if len(pairs) != 1:
            raise GraphError(
                400, "Request_UnsupportedQuery", "Continuation requires its original options"
            )
        token = pairs[0][1]
        cursor = await resolve_cursor(session, token, binding, now, expired_status=410)
        query = cursor.query
    else:
        query = options(pairs, record)
    upper = cursor.upper_revision if cursor else int(now.timestamp() * 1_000_000)
    if record:
        statement = select(AuditQueryItem).where(
            AuditQueryItem.tenant_id == actor.tenant_id,
            AuditQueryItem.query_id == row.id,
        )
        if cursor:
            statement = statement.where(
                tuple_(AuditQueryItem.event_time, AuditQueryItem.record_id)
                > (datetime.fromisoformat(cursor.last_key[0]), UUID(cursor.last_key[1]))
            )
        rows = list(
            await session.scalars(
                statement.order_by(AuditQueryItem.event_time, AuditQueryItem.record_id).limit(
                    query.top + 1
                )
            )
        )
        values = [selected(item.payload, query.select) for item in rows[: query.top]]
        last = (
            (rows[query.top - 1].event_time.isoformat(), str(rows[query.top - 1].record_id))
            if len(rows) > query.top
            else None
        )
    else:
        statement = (
            select(AuditQuery, Job)
            .join(Job, Job.id == AuditQuery.job_id)
            .where(
                AuditQuery.tenant_id == actor.tenant_id,
                AuditQuery.created_at <= datetime.fromtimestamp(upper / 1_000_000, UTC),
                AuditQuery.expires_at > now,
            )
        )
        if allowed is not None:
            statement = statement.where(
                AuditQuery.services != [], AuditQuery.services.contained_by(sorted(allowed))
            )
        if cursor:
            statement = statement.where(
                tuple_(AuditQuery.created_at, AuditQuery.id)
                > (datetime.fromisoformat(cursor.last_key[0]), UUID(cursor.last_key[1]))
            )
        rows = (
            await session.execute(
                statement.order_by(AuditQuery.created_at, AuditQuery.id).limit(query.top + 1)
            )
        ).all()
        values = [selected(query_view(q, j), query.select) for q, j in rows[: query.top]]
        last = (
            (rows[query.top - 1][0].created_at.isoformat(), str(rows[query.top - 1][0].id))
            if len(rows) > query.top
            else None
        )
    result = {"@odata.context": context_url(request, resource), "value": values}
    if last:
        expiry = (
            min(now + timedelta(hours=1), row.expires_at) if record else now + timedelta(hours=1)
        )
        state = (
            replace(cursor, last_key=last)
            if cursor
            else CursorState(binding, query, None, upper, last, "page", expiry)
        )
        handle = await issue_cursor(session, state, predecessor=token)
        result["@odata.nextLink"] = state_link(request, version, resource, handle)
    return result

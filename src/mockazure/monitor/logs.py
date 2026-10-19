"""Typed ingestion and pre-query workspace authorization over durable PostgreSQL rows."""

import json
import re
from dataclasses import fields, is_dataclass, replace
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from mockazure.monitor.log_models import LogRecord
from mockazure.monitor.tables import catalog, instant, typed_row, validate_row, wire_time
from mockazure.monitor.workspaces import Workspace
from mockazure.providers.arm.authorization import grant_query
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.models import ArmResource, RoleAssignment
from mockazure.providers.arm.scopes import parse_scope
from mockazure.providers.loganalytics.errors import LogsError
from mockazure.query.ast import Binding, Pipeline
from mockazure.query.compiler import execute_query
from mockazure.query.parser import parse_query
from mockazure.query.types import QueryError, QueryResult, QueryScope

AUDIENCE = "https://api.loganalytics.io"


async def ingest_log_rows(
    session, tenant_id, workspace_id, table, rows, source_ids, *, origin="import"
):
    if (
        origin not in {"import", "native"}
        or not 0 <= len(rows) <= 10000
        or len(rows) != len(source_ids)
    ):
        raise ValueError("Invalid log batch")
    if len(set(source_ids)) != len(source_ids) or any(
        not isinstance(s, str) or not 1 <= len(s) <= 512 for s in source_ids
    ):
        raise ValueError("Log source IDs must be distinct nonempty strings")
    if table not in catalog() or len(json.dumps(rows, allow_nan=False).encode()) > 8 * 1024 * 1024:
        raise ValueError("Unsupported table or log batch exceeds 8 MiB")
    values = [validate_row(table, row) for row in rows]
    columns = {c["name"] for c in catalog()[table]["columns"]}
    for value in values:
        for column, expected in (("TenantId", str(workspace_id)), ("Type", table)):
            if column in columns:
                if column in value and value[column] != expected:
                    raise ValueError("System log column does not match its workspace or table")
                value[column] = expected
    workspace = await session.scalar(
        select(Workspace)
        .where(Workspace.tenant_id == tenant_id, Workspace.customer_id == workspace_id)
        .with_for_update()
    )
    if workspace is None:
        raise ValueError("Workspace is not owned by this tenant")
    async with session.begin_nested():
        count = 0
        for payload, source_id in zip(values, source_ids, strict=True):
            statement = (
                insert(LogRecord)
                .values(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    table_name=table,
                    source_id=source_id,
                    origin=origin,
                    time_generated=instant(payload["TimeGenerated"]),
                    payload=payload,
                )
                .on_conflict_do_nothing(
                    index_elements=["tenant_id", "workspace_id", "table_name", "source_id"]
                )
                .returning(LogRecord.id)
            )
            if await session.scalar(statement) is not None:
                count += 1
            else:
                previous = await session.scalar(
                    select(LogRecord).where(
                        LogRecord.tenant_id == tenant_id,
                        LogRecord.workspace_id == workspace_id,
                        LogRecord.table_name == table,
                        LogRecord.source_id == source_id,
                    )
                )
                if previous.payload != payload or previous.origin != origin:
                    raise ValueError("Log source identity already has different content")
        return count


def sources(plan):
    names = set()
    bindings = {n.name for n in plan.nodes if isinstance(n, Binding)}

    def visit(node):
        if isinstance(node, Pipeline):
            names.add(node.source)
        if is_dataclass(node):
            for field in fields(node):
                visit(getattr(node, field.name))
        elif isinstance(node, tuple):
            for value in node:
                visit(value)

    visit(plan)
    return names - bindings


async def authorize_workspace(session, actor, workspace_id, tables):
    workspace = await session.scalar(
        select(Workspace).where(
            Workspace.tenant_id == actor.tenant_id, Workspace.customer_id == workspace_id
        )
    )
    if workspace is None:
        raise LogsError(403, "InsufficientAccessError", "Workspace is unavailable to this identity")
    resource = await session.get(ArmResource, (actor.tenant_id, workspace.scope_key))
    scope = parse_scope(resource.display_id)
    for table in tables:
        try:
            grants = await grant_query(
                session,
                actor,
                "Microsoft.OperationalInsights/workspaces/query/" + table + "/read",
                audience=AUDIENCE,
                delegated_scope="Data.Read",
            )
        except ArmError:
            raise LogsError(
                403, "InsufficientAccessError", "Current workspace identity is unavailable"
            ) from None
        permitted = await session.scalar(
            grants.where(RoleAssignment.scope_key.in_((*scope.parent_keys, scope.key))).limit(1)
        )
        if permitted is None:
            raise LogsError(
                403, "InsufficientAccessError", "Current identity cannot query this workspace"
            )
    return workspace


def duration(value):
    match = re.fullmatch(
        r"P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?",
        value,
    )
    if match is None or not any(match.groups()):
        raise ValueError("Unsupported ISO8601 duration")
    return timedelta(
        seconds=sum(
            float(v or 0) * scale
            for v, scale in zip(match.groups(), (86400, 3600, 60, 1), strict=True)
        )
    )


def timespan(value, now):
    if not isinstance(value, str):
        raise ValueError("Invalid timespan")
    parts = value.split("/")
    if len(parts) == 1:
        end = now
        start = end - duration(parts[0])
    elif len(parts) == 2:
        left, right = parts
        if left.startswith("P"):
            end = instant(right)
            start = end - duration(left)
        elif right.startswith("P"):
            start = instant(left)
            end = start + duration(right)
        else:
            start, end = instant(left), instant(right)
    else:
        raise ValueError("Invalid timespan")
    if start > end:
        raise ValueError("Reversed timespan")
    return start, end


async def input_tables(session, actor, workspace_ids, names, intervals, *, limits=None):
    from mockazure.query.types import QueryLimits

    limits = limits or QueryLimits()
    if not names <= catalog().keys():
        raise QueryError("Unknown workspace table")
    for workspace_id in workspace_ids:
        await authorize_workspace(session, actor, workspace_id, names)
    result = {
        name: QueryResult([(c["name"], c["type"]) for c in catalog()[name]["columns"]], [])
        for name in names
    }
    query = select(LogRecord).where(
        LogRecord.tenant_id == actor.tenant_id,
        LogRecord.workspace_id.in_(workspace_ids),
        LogRecord.table_name.in_(names),
    )
    for start, end in intervals:
        query = query.where(LogRecord.time_generated >= start, LogRecord.time_generated <= end)
    rows = await session.stream_scalars(
        query.order_by(LogRecord.time_generated, LogRecord.id).execution_options(yield_per=500)
    )
    size, count = 0, 0
    async for row in rows:
        size += len(json.dumps(row.payload, ensure_ascii=False).encode()) + 256
        count += 1
        if size > limits.bytes or count > limits.expanded:
            raise QueryError("Workspace query input limit exceeded", "QueryLimitExceeded")
        result[row.table_name].rows.append(typed_row(row.table_name, row.payload))
    return result


def cell(value, kind):
    if value is None:
        return None
    if kind == "datetime":
        return wire_time(value)
    if kind == "timespan":
        micros = round(value.total_seconds() * 1000000)
        sign = "-" if micros < 0 else ""
        seconds, fraction = divmod(abs(micros), 1000000)
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return f"{sign}{str(days) + '.' if days else ''}{hours:02}:{minutes:02}:{seconds:02}" + (
            f".{fraction:06}0" if fraction else ""
        )
    return value


async def query_workspace(
    session, actor, workspace_id, body, now, *, url_timespan=None, timeout=30
):
    from mockazure.providers.arm.contracts import validate_arm_payload

    # The original Python Logs SDK serializes unset optional fields as null.
    # Normalize only these optional omissions before strict schema validation.
    if isinstance(body, dict):
        body = {
            k: v for k, v in body.items() if k not in {"timespan", "workspaces"} or v is not None
        }
    validate_arm_payload("QueryBody", body, namespace="loganalytics")
    if set(body) - {"query", "timespan", "workspaces"}:
        raise QueryError("Unsupported Logs query field")
    ids = tuple(
        dict.fromkeys([UUID(str(workspace_id)), *(UUID(w) for w in body.get("workspaces", []))])
    )
    if len(ids) > 100:
        raise QueryError("At most 100 workspaces are supported")
    intervals = [timespan(v, now) for v in (body.get("timespan"), url_timespan) if v is not None]
    plan = parse_query(body["query"], "loganalytics")
    plan = replace(plan, parameters={"intervals": intervals, "timeout": timeout})
    result = await execute_query(
        session,
        actor,
        QueryScope(actor.tenant_id, workspace_ids=tuple(str(w) for w in ids)),
        plan,
        now,
    )
    payload = {
        "tables": [
            {
                "name": "PrimaryResult",
                "columns": [{"name": n, "type": k} for n, k in result.columns],
                "rows": [
                    [cell(v, kind) for v, (_, kind) in zip(row, result.columns, strict=True)]
                    for row in result.rows
                ],
            }
        ]
    }
    return payload


async def metadata(session, actor, workspace_id):
    await authorize_workspace(session, actor, workspace_id, catalog())
    return {
        "tables": [
            {
                "id": name,
                "name": name,
                "timespanColumn": "TimeGenerated",
                "columns": item["columns"],
            }
            for name, item in catalog().items()
        ]
    }

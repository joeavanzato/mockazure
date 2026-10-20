"""Graph hunting profile over the shared bounded KQL evaluator."""

import json
from dataclasses import asdict, replace
from datetime import timedelta
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import select

from mockazure.evidence.clock import evidence_now
from mockazure.hunting.authorization import authorize_inputs, visible_columns
from mockazure.hunting.models import HuntingRecord
from mockazure.hunting.wire import hunting_response
from mockazure.monitor.logs import sources, timespan
from mockazure.monitor.tables import instant
from mockazure.query.compiler import execute_query
from mockazure.query.parser import parse_query
from mockazure.query.types import QueryError, QueryLimits, QueryResult, QueryScope


class HuntingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    Query: str = Field(
        min_length=1,
        max_length=65536,
        strict=True,
        validation_alias=AliasChoices("Query", "query"),
    )
    Timespan: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        strict=True,
        validation_alias=AliasChoices("Timespan", "timespan"),
    )
    workspaceId: UUID | None = None


async def input_tables(session, actor, requested_workspace, plan, now, *, limits):
    names = sources(plan)
    scope = await authorize_inputs(session, actor, names, requested_workspace)
    start, end = timespan(plan.parameters.get("timespan") or "P30D", now)
    # Local native Defender retention is 30 days. Explicit UEBA/Sentinel mode
    # supports retained fixtures up to 366 days; it never opens arbitrary Logs tables.
    start = max(start, now - timedelta(days=366 if "sentinel-ueba" in scope.entitlements else 30))
    end = min(end, now)
    columns = {name: visible_columns(name, scope) for name in names}
    result = {name: QueryResult(list(fields.items()), []) for name, fields in columns.items()}
    statement = select(HuntingRecord).where(
        HuntingRecord.tenant_id == actor.tenant_id,
        HuntingRecord.table_name.in_(names),
        HuntingRecord.timestamp >= start,
        HuntingRecord.timestamp <= end,
        HuntingRecord.available_at <= now,
    )
    if scope.device_ids is not None:
        statement = statement.where(
            ~HuntingRecord.table_name.like("Device%")
            | HuntingRecord.device_id.in_(scope.device_ids),
        )
    rows = await session.stream_scalars(
        statement.order_by(
            HuntingRecord.timestamp,
            HuntingRecord.table_name,
            HuntingRecord.source_id,
        ).execution_options(yield_per=16)
    )
    size, count = 0, 0
    async for row in rows:
        size += len(json.dumps(row.payload, ensure_ascii=False).encode()) + 256
        count += 1
        if size > limits.bytes or count > limits.expanded:
            raise QueryError("Hunting query input limit exceeded", "QueryLimitExceeded")
        fields = columns[row.table_name]
        result[row.table_name].rows.append(
            [
                instant(row.payload[name])
                if kind == "datetime" and row.payload.get(name) is not None
                else row.payload.get(name)
                for name, kind in fields.items()
            ]
        )
    return result


async def run_hunting(
    session,
    actor,
    request,
    *,
    wall_now,
    limits: QueryLimits | None = None,
    include_reference=False,
):
    request = HuntingRequest.model_validate(request)
    now = await evidence_now(session, actor.tenant_id, wall_now)
    plan = replace(parse_query(request.Query, "hunting"), parameters={"timespan": request.Timespan})
    previous = session.info.get("query_limits")
    defaults = asdict(QueryLimits())
    requested = asdict(limits) if limits is not None else (previous or {})
    bounded = {key: min(value, requested.get(key, value)) for key, value in defaults.items()}
    if any(value <= 0 for value in bounded.values()):
        raise QueryError("Query limits must be positive")
    session.info["query_limits"] = bounded
    try:
        result = await execute_query(
            session,
            actor,
            QueryScope(
                actor.tenant_id,
                workspace_ids=((str(request.workspaceId),) if request.workspaceId else ()),
            ),
            plan,
            now,
        )
        response = hunting_response(result)
        return (response, now) if include_reference else response
    finally:
        if previous is None:
            session.info.pop("query_limits", None)
        else:
            session.info["query_limits"] = previous

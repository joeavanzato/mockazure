"""Intune-specific bounded query profiles and revision-stable pages."""

import copy
import re
from datetime import UTC, datetime

from sqlalchemy import DateTime, cast

from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import context_url
from mockazure.providers.graph.query.parser import selection
from mockazure.providers.graph.schema_reference import schema
from mockazure.workloads.cursors import binding, issue_cursor, resolve_cursor
from mockazure.workloads.intune_contracts import CONFIGURATIONS, POLICIES, SCOPE
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.queries import PAGE_BYTES, invalid, link, value_size
from mockazure.workloads.repository import scope_row


def project(row, selected):
    return copy.deepcopy(
        {
            key: value
            for key, value in row.payload.items()
            if selected is None or key in selected or key in {"id", "@odata.type"}
        }
    )


def options(request, kind, item):
    pairs = list(request.query_params.multi_items())
    values = dict(pairs)
    allowed = {"$select"} if item else {"$select", "$top", "$filter", "$orderby"}
    if (
        len(pairs) != len(values)
        or values.keys() - allowed
        or sum(len(k) + len(v) for k, v in pairs) > 16384
    ):
        invalid("Unsupported Intune query option")
    types = schema("v1.0")["types"]
    names = (
        CONFIGURATIONS
        if kind == "deviceConfiguration"
        else POLICIES
        if kind == "deviceCompliancePolicy"
        else {kind}
    )
    fields = {key for name in names for key in types[name]["properties"]}
    selected = list(selection(values["$select"], fields)) if "$select" in values else None
    top = values.get("$top", "100")
    if not top.isascii() or not top.isdigit() or len(top) > 4 or not 1 <= int(top) <= 1000:
        invalid("Intune page size must be 1..1000")
    supported = {
        "id",
        "displayName",
        "deviceName",
        "operatingSystem",
        "userId",
        "azureADDeviceId",
        "complianceState",
        "status",
    } & fields
    condition = None
    if "$filter" in values:
        match = re.fullmatch(r"([A-Za-z]+) eq '((?:[^']|'')*)'", values["$filter"])
        if not match or match[1] not in supported:
            invalid("Unsupported Intune equality filter")
        condition = [match[1], match[2].replace("''", "'")]
    order = values.get("$orderby", "id").split()
    if (
        not 1 <= len(order) <= 2
        or order[0]
        not in (
            {"id", "displayName", "deviceName", "lastSyncDateTime", "lastReportedDateTime"} & fields
        )
        or (len(order) == 2 and order[1] not in {"asc", "desc"})
    ):
        invalid("Unsupported Intune ordering")
    return {"top": int(top), "select": selected, "filter": condition, "order": order}


async def page_state(request, session, actor, kind, item):
    token = request.query_params.get("$skiptoken")
    row = await scope_row(session, actor.tenant_id, "intune", SCOPE, lock="read")
    if token is not None:
        if item or len(request.query_params.multi_items()) != 1:
            invalid("A continuation cannot change its query")
        state = await resolve_cursor(
            session, actor, request.scope["path"], token, datetime.now(UTC)
        )
        if row is None or str(row.generation) != state["generation"]:
            raise GraphError(410, "resyncRequired", "Intune evidence was reset")
        return state, token
    return {
        "workload": "intune",
        "binding": binding(actor, request.scope["path"]),
        "revision": row.revision if row else 0,
        "scopes": {SCOPE: {"upper": row.revision, "generation": str(row.generation)}}
        if row
        else {},
        "generation": str(row.generation) if row else None,
        "offset": 0,
        "options": options(request, kind, item),
    }, None


async def collection(request, session, actor, query, state, token, kind):
    opts = state["options"]
    if opts["filter"]:
        field, value = opts["filter"]
        query = query.where(V.payload[field].as_string() == value)
    field, *direction = opts["order"]
    order = V.payload[field].as_string()
    if field.endswith("DateTime"):
        order = cast(order, DateTime(timezone=True))
    order = order.desc() if direction == ["desc"] else order.asc()
    rows = await session.stream_scalars(
        query.order_by(order.nulls_last(), V.id)
        .offset(state["offset"])
        .limit(opts["top"] + 1)
        .execution_options(yield_per=1)
    )
    values, size, more = [], 0, False
    try:
        async for row in rows:
            value = project(row, opts["select"])
            length = value_size(value)
            if len(values) == opts["top"] or size + length > PAGE_BYTES:
                if not values:
                    raise GraphError(
                        413, "responseTooLarge", "One Intune record exceeds the response budget"
                    )
                more = True
                break
            values.append(value)
            size += length
    finally:
        await rows.close()
    result = {"@odata.context": context_url(request, kind), "value": values}
    if more:
        state = copy.deepcopy(state)
        state["offset"] += len(values)
        handle = await issue_cursor(session, actor, state, datetime.now(UTC), token)
        result["@odata.nextLink"] = link(request, request.scope["path"], handle)
    return result

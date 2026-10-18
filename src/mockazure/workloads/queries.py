"""Bounded workload query options and snapshot paging shared by Graph adapters."""

import copy
import json
from datetime import UTC, datetime
from urllib.parse import quote, urlencode

from sqlalchemy import and_, or_, select

from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import context_url
from mockazure.providers.graph.query.parser import selection
from mockazure.providers.graph.schema_reference import schema
from mockazure.workloads.cursors import binding, issue_cursor, resolve_cursor
from mockazure.workloads.models import WorkloadScope as S
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.repository import scope_row, visible
from mockazure.workloads.resource_access import access_predicate


def invalid(message="Unsupported workload query option"):
    raise GraphError(400, "invalidRequest", message)


def options(request, kind, *, item=False, search=False, delta=False):
    pairs = list(request.query_params.multi_items())
    values = dict(pairs)
    allowed = {"$select", "$expand"} if item else {"$select", "$top", "$orderby", "$expand"}
    if search:
        allowed.add("search")
    if delta:
        allowed |= {"token"}
        allowed -= {"$orderby", "$expand"}
    if (
        len(pairs) != len(values)
        or values.keys() - allowed
        or sum(len(k) + len(v) for k, v in pairs) > 16384
    ):
        invalid()
    top = values.get("$top", "100")
    if not top.isascii() or not top.isdigit() or len(top) > 4 or not 1 <= int(top) <= 1000:
        invalid("Page size must be1..1000")
    selected = (
        list(selection(values["$select"], schema("v1.0")["types"][kind]["properties"]))
        if "$select" in values and kind != "fieldValueSet"
        else (values["$select"].split(",") if "$select" in values else None)
    )
    if selected and (
        len(selected) > 256 or any(not part or len(part) > 256 or "@" in part for part in selected)
    ):
        invalid("Invalid selected field")
    expanded = values.get("$expand")
    if expanded is not None and not (
        kind == "listItem"
        and (
            expanded == "fields"
            or (expanded.startswith("fields($select=") and expanded.endswith(")"))
        )
    ):
        invalid("Only list-item fields expansion is supported")
    order = values.get("$orderby", "id").split()
    if (
        not 1 <= len(order) <= 2
        or order[0] not in {"id", "name", "lastModifiedDateTime"}
        or (len(order) == 2 and order[1] not in {"asc", "desc"})
    ):
        invalid("Unsupported ordering")
    if values.get("token") not in {None, "latest"}:
        invalid("Only token=latest is supported")
    term = values.get("search")
    if search and (term is None or not 1 <= len(term) <= 256):
        invalid("Site search requires a bounded search term")
    return {
        "select": selected,
        "expand": expanded,
        "top": int(top),
        "order": order,
        "search": term,
        "latest": values.get("token") == "latest",
    }


def project(row, selected):
    value = copy.deepcopy(row.payload)
    return {
        key: val for key, val in value.items() if selected is None or key in selected or key == "id"
    }


def link(request, resource, token, delta=False):
    origin = request.app.state.settings.advertised_endpoints(request.headers.get("host", "")).graph
    return (
        origin
        + quote(resource, safe="/,:!()$='@")
        + "?"
        + urlencode({"token" if delta else "$skiptoken": token})
    )


async def page_state(
    request, session, actor, kind, operation, scope=None, *, search=False, delta=False
):
    resource = request.scope["path"]
    values = dict(request.query_params)
    token = values.get("$skiptoken") or values.get("$deltatoken")
    if delta and values.get("token") not in {None, "latest"}:
        token = values["token"]
    if token:
        if len(request.query_params.multi_items()) != 1:
            invalid("A continuation cannot change its query")
        state = await resolve_cursor(session, actor, resource, token, datetime.now(UTC))
        for name, saved in state["scopes"].items():
            row = await scope_row(session, actor.tenant_id, "sharepoint", name, lock="read")
            if row is None or str(row.generation) != saved["generation"]:
                raise GraphError(410, "resyncRequired", "Workload state was reset")
            if row.grant_revision != saved["grants"] or not await session.scalar(
                select(access_predicate(actor, name, operation))
            ):
                raise GraphError(403, "accessDenied", "Resource access changed; restart collection")
        return state, token
    opts = options(request, kind, search=search, delta=delta)
    query = select(S).where(
        S.tenant_id == actor.tenant_id,
        S.workload == "sharepoint",
        access_predicate(actor, S.scope, operation),
    )
    if scope is not None:
        query = query.where(S.scope == scope)
    scopes = list(
        await session.scalars(query.order_by(S.scope).limit(10001).with_for_update(read=True))
    )
    if len(scopes) > 10000:
        raise GraphError(
            413, "responseTooLarge", "Collect explicit sites instead of more than10000 site scopes"
        )
    state = {
        "binding": binding(actor, resource),
        "options": opts,
        "offset": 0,
        "scopes": {
            row.scope: {
                "upper": row.revision,
                "generation": str(row.generation),
                "grants": row.grant_revision,
            }
            for row in scopes
        },
    }
    return state, None


def snapshots(state):
    clauses = [
        and_(V.scope == name, visible(V, saved["upper"])) for name, saved in state["scopes"].items()
    ]
    return or_(*clauses) if clauses else False


PAGE_BYTES = 8 * 1024 * 1024 - 16 * 1024


def value_size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) + 2


async def collection_response(request, session, actor, query, kind, state, token, render=None):
    opts = state["options"]
    field = (
        V.id
        if opts["order"][0] == "id"
        else V.name
        if opts["order"][0] == "name"
        else V.payload["lastModifiedDateTime"].as_string()
    )
    ordering = field.desc() if opts["order"][-1] == "desc" else field.asc()
    rows = await session.stream_scalars(
        query.order_by(ordering.nulls_last(), V.id)
        .offset(state["offset"])
        .limit(opts["top"] + 1)
        .execution_options(yield_per=1)
    )
    values, size, more = [], 0, False
    try:
        async for row in rows:
            value = await render(row, opts) if render else project(row, opts["select"])
            length = value_size(value)
            if len(values) == opts["top"] or size + length > PAGE_BYTES:
                if not values:
                    raise GraphError(
                        413, "responseTooLarge", "One record exceeds the page byte budget"
                    )
                more = True
                break
            values.append(value)
            size += length
    finally:
        await rows.close()
    result = {"@odata.context": context_url(request, kind), "value": values}
    if more:
        state["offset"] += len(values)
        handle = await issue_cursor(session, actor, state, datetime.now(UTC), token)
        result["@odata.nextLink"] = link(request, request.scope["path"], handle)
    return result

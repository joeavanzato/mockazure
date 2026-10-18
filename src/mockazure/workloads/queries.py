"""Bounded workload query options and snapshot paging shared by Graph adapters."""

import copy
from datetime import UTC, datetime
from urllib.parse import urlencode

from sqlalchemy import and_, or_, select

from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import context_url
from mockazure.providers.graph.query.parser import selection
from mockazure.providers.graph.schema_reference import schema
from mockazure.workloads.cursors import binding, issue_cursor, resolve_cursor
from mockazure.workloads.models import WorkloadScope as S
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.repository import visible
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
    return origin + resource + "?" + urlencode({"$deltatoken" if delta else "$skiptoken": token})


async def page_state(
    request, session, actor, kind, operation, scope=None, *, search=False, delta=False
):
    resource = request.url.path
    values = dict(request.query_params)
    token = values.get("$skiptoken") or values.get("$deltatoken")
    if token:
        if len(request.query_params.multi_items()) != 1:
            invalid("A continuation cannot change its query")
        state = await resolve_cursor(session, actor, resource, token, datetime.now(UTC))
        for name, saved in state["scopes"].items():
            row = await session.get(S, (actor.tenant_id, "sharepoint", name))
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
    scopes = list(await session.scalars(query.order_by(S.scope).limit(10001)))
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
    rows = list(
        await session.scalars(
            query.order_by(ordering.nulls_last(), V.id)
            .offset(state["offset"])
            .limit(opts["top"] + 1)
        )
    )
    result = {
        "@odata.context": context_url(request, kind),
        "value": [
            await render(row, opts) if render else project(row, opts["select"])
            for row in rows[: opts["top"]]
        ],
    }
    if len(rows) > opts["top"]:
        state["offset"] += opts["top"]
        handle = await issue_cursor(session, actor, state, datetime.now(UTC), token)
        result["@odata.nextLink"] = link(request, request.url.path, handle)
    return result

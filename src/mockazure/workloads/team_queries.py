"""Bounded Teams-specific OData profiles and durable snapshot continuations."""

import copy
import re
from datetime import UTC, datetime

from sqlalchemy import DateTime, and_, case, cast, func, select
from sqlalchemy.orm import aliased

from mockazure.authorization.evaluator import AuthorizationDenied, require_current_identity
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import context_url
from mockazure.providers.graph.query.parser import selection
from mockazure.providers.graph.schema_reference import schema
from mockazure.workloads.cursors import binding, issue_cursor, resolve_cursor
from mockazure.workloads.models import WorkloadRelation as E
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.queries import PAGE_BYTES, invalid, link, project, value_size
from mockazure.workloads.repository import scope_row, visible


def options(request, profile, kind):
    pairs = list(request.query_params.multi_items())
    values = dict(pairs)
    allowed = {
        "channelMessages": {"$top", "$expand"},
        "replies": {"$top"},
        "chatMessages": {"$top", "$orderby", "$filter"},
        "members": {"$top", "$filter", "$select"},
        "channels": {"$filter", "$select"},
        "team": {"$select"},
        "chats": {"$top", "$filter", "$orderby"},
    }.get(profile, set())
    if (
        len(pairs) != len(values)
        or values.keys() - allowed
        or sum(len(k) + len(v) for k, v in pairs) > 16384
    ):
        invalid("Unsupported query option for this Teams operation")
    maximum = 50 if profile in {"channelMessages", "chatMessages", "replies", "chats"} else 999
    default = 20 if profile == "channelMessages" else min(100, maximum)
    top = values.get("$top", str(default))
    if not top.isascii() or not top.isdigit() or len(top) > 3 or not 1 <= int(top) <= maximum:
        invalid("Invalid Teams page size")
    selected = (
        list(selection(values["$select"], schema("v1.0")["types"][kind]["properties"]))
        if "$select" in values
        else None
    )
    expand = values.get("$expand")
    if expand is not None and expand != "replies":
        invalid("Only channel-message replies expansion is supported")
    order = values.get("$orderby")
    if order is not None and order not in (
        {"lastModifiedDateTime desc", "createdDateTime desc"}
        if profile == "chatMessages"
        else {"lastMessagePreview/createdDateTime desc"}
    ):
        invalid("Unsupported Teams ordering")
    if order is None and profile in {"chatMessages", "channelMessages"}:
        order = "lastModifiedDateTime desc"
    filters = []
    expression = values.get("$filter")
    if expression and profile == "chatMessages":
        for part in expression.split(" and "):
            match = re.fullmatch(r"(lastModifiedDateTime|createdDateTime) (gt|lt) (\S+)", part)
            if not match or (match[1] == "createdDateTime" and match[2] != "lt"):
                invalid("Unsupported message date filter")
            try:
                date = datetime.fromisoformat(match[3].replace("Z", "+00:00"))
                if date.tzinfo is None:
                    raise ValueError()
            except ValueError:
                invalid("Message date filters require timezone-aware timestamps")
            # Microsoft ignores filters unless an explicit matching order is supplied.
            if values.get("$orderby", "").split(" ")[0] == match[1]:
                filters.append([match[1], match[2], date.isoformat()])
    elif expression:
        match = re.fullmatch(
            r"(id|userId|membershipType|displayName|chatType) eq '((?:[^']|'')*)'", expression
        )
        fields = {
            "members": {"id", "userId"},
            "channels": {"id", "membershipType", "displayName"},
            "chats": {"id", "chatType"},
        }
        if not match or match[1] not in fields.get(profile, set()):
            invalid("Unsupported Teams equality filter")
        filters.append([match[1], "eq", match[2].replace("''", "'")])
    return {
        "top": int(top),
        "select": selected,
        "expand": expand,
        "order": order,
        "filters": filters,
    }


async def continuation_preflight(request, session, actor):
    token = request.query_params.get("$skiptoken")
    if token is None:
        return
    try:
        await require_current_identity(actor, session)
    except AuthorizationDenied as exc:
        raise GraphError(403, "Forbidden", str(exc)) from exc
    if len(request.query_params.multi_items()) != 1:
        invalid("A continuation cannot change its query")
    state = await resolve_cursor(session, actor, request.scope["path"], token, datetime.now(UTC))
    # Validate the bound snapshot before reset can hide its current navigation root.
    # Hold these leases through dispatch; ordinary operation/membership checks still apply.
    for scope, saved in sorted(state["scopes"].items()):
        row = await scope_row(session, actor.tenant_id, "teams", scope, lock="read")
        if row is None or str(row.generation) != saved["generation"]:
            raise GraphError(410, "resyncRequired", "Conversation state was reset")


async def page_state(request, session, actor, scope, profile, kind):
    resource = request.scope["path"]
    token = request.query_params.get("$skiptoken")
    state_row = await scope_row(session, actor.tenant_id, "teams", scope, lock="read")
    if token:
        if len(request.query_params.multi_items()) != 1:
            invalid("A continuation cannot change its query")
        state = await resolve_cursor(session, actor, resource, token, datetime.now(UTC))
        saved = state["scopes"].get(scope)
        if saved is None or str(state_row.generation) != saved["generation"]:
            raise GraphError(410, "resyncRequired", "Conversation state was reset")
        if saved["grants"] != state_row.grant_revision:
            raise GraphError(403, "Forbidden", "Conversation access changed; restart collection")
        return state, token
    return {
        "workload": "teams",
        "binding": binding(actor, resource),
        "offset": 0,
        "options": options(request, profile, kind),
        "scopes": {
            scope: {
                "upper": state_row.revision,
                "generation": str(state_row.generation),
                "grants": state_row.grant_revision,
            }
        },
    }, None


def children(actor, scope, source, relation, revision):
    return (
        select(V)
        .join(
            E,
            and_(
                E.tenant_id == V.tenant_id,
                E.workload == V.workload,
                E.scope == V.scope,
                E.target_kind == V.kind,
                E.target_id == V.id,
            ),
        )
        .where(
            V.tenant_id == actor.tenant_id,
            V.workload == "teams",
            V.scope == scope,
            V.deleted.is_(False),
            visible(V, revision),
            visible(E, revision),
            E.source_kind == source.kind,
            E.source_id == source.id,
            E.relation == relation,
        )
    )


def ordering(actor, scope, revision, opts, profile, scopes):
    field = (opts["order"] or "id").split(" ")[0]
    if field == "id":
        return (V.payload["id"].as_string(), V.id)
    if field == "lastMessagePreview/createdDateTime":
        message, edge = aliased(V), aliased(E)
        saved_revision = case(
            {name: saved["upper"] for name, saved in scopes.items()}, value=V.scope
        )
        value = (
            select(
                func.max(
                    cast(message.payload["createdDateTime"].as_string(), DateTime(timezone=True))
                )
            )
            .join(
                edge,
                and_(
                    edge.tenant_id == message.tenant_id,
                    edge.workload == message.workload,
                    edge.scope == message.scope,
                    edge.target_kind == message.kind,
                    edge.target_id == message.id,
                ),
            )
            .where(
                message.tenant_id == actor.tenant_id,
                message.workload == "teams",
                message.scope == V.scope,
                message.deleted.is_(False),
                visible(message, saved_revision),
                visible(edge, saved_revision),
                edge.source_kind == "chat",
                edge.source_id == V.id,
                edge.relation == "messages",
            )
            .correlate(V)
            .scalar_subquery()
        )
    else:
        value = cast(V.payload[field].as_string(), DateTime(timezone=True))
    if profile == "channelMessages":
        reply, edge = aliased(V), aliased(E)
        last_reply = (
            select(
                func.max(
                    cast(reply.payload["lastModifiedDateTime"].as_string(), DateTime(timezone=True))
                )
            )
            .join(
                edge,
                and_(
                    edge.tenant_id == reply.tenant_id,
                    edge.workload == reply.workload,
                    edge.scope == reply.scope,
                    edge.target_kind == reply.kind,
                    edge.target_id == reply.id,
                ),
            )
            .where(
                reply.tenant_id == actor.tenant_id,
                reply.workload == "teams",
                reply.scope == scope,
                reply.deleted.is_(False),
                visible(reply, revision),
                visible(edge, revision),
                edge.source_kind == "chatMessage",
                edge.source_id == V.id,
                edge.relation == "replies",
            )
            .correlate(V)
            .scalar_subquery()
        )
        value = func.greatest(value, last_reply)
    return (value.desc().nulls_last(), V.id)


async def collection(
    request, session, actor, query, state, token, kind, profile, scope, render=None, resource=None
):
    opts = state["options"]
    revision = state["scopes"][scope]["upper"]
    for field, operation, value in opts["filters"]:
        column = V.payload[field].as_string()
        if operation == "eq":
            query = query.where(column == value)
        else:
            column = cast(column, DateTime(timezone=True))
            date = datetime.fromisoformat(value)
            query = query.where(column > date if operation == "gt" else column < date)
    rows = await session.stream_scalars(
        query.order_by(*ordering(actor, scope, revision, opts, profile, state["scopes"]))
        .offset(state["offset"])
        .limit(opts["top"] + 1)
        .execution_options(yield_per=1)
    )
    values, size, more = [], 0, False
    try:
        async for row in rows:
            value = await render(row) if render else project(row, opts["select"])
            length = value_size(value)
            if len(values) == opts["top"] or size + length > PAGE_BYTES:
                if not values:
                    raise GraphError(
                        413, "responseTooLarge", "One Teams record exceeds the response budget"
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
        if profile == "replies":
            state["options"]["top"] = min(50, state["options"]["top"])
        handle = await issue_cursor(session, actor, state, datetime.now(UTC), token)
        result["@odata.nextLink"] = link(request, resource or request.scope["path"], handle)
    return result

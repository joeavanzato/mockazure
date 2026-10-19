"""Opaque query/scope/options-bound continuation with explicit result invalidation."""

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta

from mockazure.cloud.models import CloudState
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.graph.continuations import (
    CursorState,
    binding_for,
    issue_cursor,
    resolve_cursor,
)
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.query.ast import QuerySpec
from mockazure.query.compiler import json_value


async def page_state(session, actor, body, now):
    options = {"$top": 1000, "$skip": 0, "resultFormat": "objectArray"} | body.get("options", {})
    token = options.pop("$skipToken", None)
    cloud = await session.get(CloudState, actor.tenant_id)
    identity = {
        "query": body["query"],
        "subscriptions": sorted(set(body.get("subscriptions", []))),
        "options": options,
        "generation": cloud.generation if cloud else 0,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    binding = await binding_for(session, actor, "2024-04-01", "resourcegraph:" + digest)
    if token:
        try:
            state = await resolve_cursor(session, token, binding, now)
        except GraphError:
            raise ArmError(
                400,
                "InvalidSkipToken",
                "Query continuation no longer matches its current scope or options",
            ) from None
        return state, datetime.fromisoformat(state.last_key[1]), token
    return (
        CursorState(
            binding,
            QuerySpec(top=options["$top"]),
            None,
            0,
            (options["$skip"], now.isoformat(), None),
            "page",
            now + timedelta(hours=1),
        ),
        now,
        None,
    )


async def page_result(session, state, result, predecessor):
    fingerprint = hashlib.sha256(
        json.dumps(
            [result.columns, result.rows], default=json_value, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()
    offset, instant, previous = state.last_key
    if previous is not None and previous != fingerprint:
        raise ArmError(400, "InvalidSkipToken", "Query results changed; restart the query")
    rows = result.rows[offset : offset + state.query.top]
    more = offset + len(rows) < len(result.rows)
    token = None
    if more:
        token = await issue_cursor(
            session,
            replace(state, last_key=(offset + len(rows), instant, fingerprint)),
            predecessor=predecessor,
        )
    return rows, more, token

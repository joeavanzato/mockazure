"""Durable opaque continuations with actor, query, resource and grant binding."""

import copy
import hashlib
import hmac
import json
import secrets
from datetime import timedelta

from sqlalchemy.dialects.postgresql import insert

from mockazure.providers.graph.errors import GraphError
from mockazure.workloads.models import WorkloadCursor


def binding(actor, resource):
    return {
        "tenant": str(actor.tenant_id),
        "client": str(actor.client_id),
        "subject": str(actor.subject_id) if actor.subject_id else None,
        "kind": actor.token_kind,
        "permissions": sorted(actor.permissions),
        "resource": resource,
    }


async def issue_cursor(session, actor, state, now, predecessor=None):
    value = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    token = (
        hmac.new(predecessor.encode(), value, "sha256").hexdigest()
        if predecessor
        else secrets.token_hex(32)
    )
    await session.execute(
        insert(WorkloadCursor)
        .values(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            tenant_id=actor.tenant_id,
            workload="sharepoint",
            state=state,
            expires_at=now + timedelta(hours=1),
        )
        .on_conflict_do_nothing()
    )
    return token


async def resolve_cursor(session, actor, resource, token, now):
    if len(token) != 64 or not token.isascii():
        raise GraphError(400, "invalidRequest", "Invalid workload continuation")
    row = await session.get(WorkloadCursor, hashlib.sha256(token.encode()).hexdigest())
    if (
        row is None
        or row.tenant_id != actor.tenant_id
        or row.state["binding"] != binding(actor, resource)
    ):
        raise GraphError(400, "invalidRequest", "Continuation binding does not match")
    if row.expires_at <= now:
        raise GraphError(410, "resyncRequired", "Continuation expired; restart collection")
    return copy.deepcopy(row.state)

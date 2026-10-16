"""Opaque, tenant/principal-bound state links shared by all API replicas."""

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from mockazure.directory.models import Continuation, DirectoryState
from mockazure.identity.types import PrincipalContext
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.query import ast
from mockazure.providers.graph.query.ast import QuerySpec


class MissingCursor(GraphError):
    def __init__(self):
        super().__init__(400, "Request_UnsupportedQuery", "Continuation state is unavailable")


NODE_TYPES = {
    kind.__name__: kind
    for kind in (
        ast.Field,
        ast.Literal,
        ast.Compare,
        ast.Boolean,
        ast.In,
        ast.Any,
        ast.StartsWith,
        ast.OrderTerm,
        ast.ExpandSpec,
        ast.QuerySpec,
    )
}


@dataclass(frozen=True)
class CursorBinding:
    tenant_id: UUID
    generation: UUID
    version: str
    resource: str
    client_id: UUID
    subject_id: UUID | None
    token_kind: str
    permissions_hash: str


@dataclass(frozen=True)
class CursorState:
    binding: CursorBinding
    query: QuerySpec
    lower_revision: int | None
    upper_revision: int
    last_key: tuple | None
    kind: Literal["page", "delta"]
    expires_at: datetime


def encode_node(value):
    if is_dataclass(value):
        return {
            "node": type(value).__name__,
            "fields": {
                field.name: encode_node(getattr(value, field.name)) for field in fields(value)
            },
        }
    if isinstance(value, tuple):
        return [encode_node(item) for item in value]
    return value


def decode_node(value):
    if isinstance(value, dict):
        return NODE_TYPES[value["node"]](
            **{key: decode_node(item) for key, item in value["fields"].items()}
        )
    if isinstance(value, list):
        return tuple(decode_node(item) for item in value)
    return value


async def binding_for(
    session, context: PrincipalContext, version: str, resource: str
) -> CursorBinding:
    await session.execute(
        insert(DirectoryState)
        .values(tenant_id=context.tenant_id, revision=0, generation=uuid4())
        .on_conflict_do_nothing(index_elements=["tenant_id"])
    )
    generation = await session.scalar(
        select(DirectoryState.generation).where(DirectoryState.tenant_id == context.tenant_id)
    )
    return CursorBinding(
        context.tenant_id,
        generation,
        version,
        resource,
        context.client_id,
        context.subject_id,
        context.token_kind,
        hashlib.sha256("\0".join(sorted(context.permissions)).encode()).hexdigest(),
    )


def encode_state(state: CursorState) -> dict:
    binding = {
        key: str(value) if isinstance(value, UUID) else value
        for key, value in asdict(state.binding).items()
    }
    return {
        "schema_version": 1,
        "binding": binding,
        "query": encode_node(state.query),
        "lower_revision": state.lower_revision,
        "upper_revision": state.upper_revision,
        "last_key": list(state.last_key) if state.last_key is not None else None,
        "kind": state.kind,
        "expires_at": state.expires_at.isoformat(),
    }


def decode_state(data: dict) -> CursorState:
    if data["schema_version"] != 1:
        raise ValueError("Unsupported cursor schema")
    binding = dict(data["binding"])
    for key in ("tenant_id", "generation", "client_id", "subject_id"):
        binding[key] = UUID(binding[key]) if binding[key] is not None else None
    return CursorState(
        CursorBinding(**binding),
        decode_node(data["query"]),
        data["lower_revision"],
        data["upper_revision"],
        tuple(data["last_key"]) if data["last_key"] is not None else None,
        data["kind"],
        datetime.fromisoformat(data["expires_at"]),
    )


async def issue_cursor(session, state: CursorState, *, predecessor: str | None = None) -> str:
    payload = encode_state(state)
    if predecessor is None:
        raw = secrets.token_bytes(32)
    else:
        # Replaying a page yields the same next link without storing a raw handle or instance key.
        message = (
            b"mockazure-directory-page-v1\0"
            + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        )
        raw = hmac.digest(predecessor.encode("ascii"), message, "sha256")
    token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    await session.execute(
        insert(Continuation)
        .values(
            token_hash=token_hash,
            tenant_id=state.binding.tenant_id,
            state=payload,
            expires_at=state.expires_at,
        )
        .on_conflict_do_nothing(index_elements=["token_hash"])
    )
    return token


async def resolve_cursor(session, token: str, binding: CursorBinding, now: datetime) -> CursorState:
    def invalid():
        raise GraphError(
            400, "Request_UnsupportedQuery", "Continuation is invalid for this request"
        )

    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        invalid()
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    row = await session.scalar(
        select(Continuation).where(
            Continuation.token_hash == token_hash, Continuation.tenant_id == binding.tenant_id
        )
    )
    if row is None:
        raise MissingCursor()
    try:
        state = decode_state(row.state)
    except (ValueError, TypeError, KeyError):
        invalid()
    current_generation = await session.scalar(
        select(DirectoryState.generation).where(DirectoryState.tenant_id == binding.tenant_id)
    )
    if state.binding != binding or current_generation != binding.generation:
        invalid()
    if row.expires_at <= now:
        if state.kind == "delta" or state.binding.resource.endswith("/delta"):
            error = GraphError(
                410, "syncStateNotFound", "Delta state expired; restart synchronization"
            )
            error.cursor_query = state.query
            raise error
        raise GraphError(
            400, "Request_UnsupportedQuery", "Directory page expired; restart collection"
        )
    return state


def state_link(request, version: str, resource: str, token: str, *, delta=False) -> str:
    profile = request.app.state.settings.advertised_endpoints(request.headers.get("host", ""))
    return f"{profile.graph}/{version}/{resource}?" + urlencode(
        {"$deltatoken" if delta else "$skiptoken": token}
    )

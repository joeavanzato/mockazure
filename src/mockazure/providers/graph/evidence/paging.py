"""Persisted evidence snapshots use publication revisions, never directory revisions."""

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert

from mockazure.evidence.models import EvidenceCursor, EvidenceVersion
from mockazure.evidence.repository import lock_state
from mockazure.evidence.schemas import EvidenceSettings
from mockazure.providers.graph.continuations import decode_node, encode_node
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.evidence.contracts import PATHS, evidence_contract
from mockazure.providers.graph.evidence.permissions import EvidenceOperation, authorize_evidence
from mockazure.providers.graph.evidence.query import compile_evidence_filter, contains_event_type
from mockazure.providers.graph.evidence.serialization import disclosed_payload, serialize_payload
from mockazure.providers.graph.query.ast import QuerySpec


@dataclass(frozen=True)
class EvidenceSnapshot:
    generation: UUID
    revision: int
    evidence_time: datetime
    retention_cutoff: datetime
    expires_at: datetime


@dataclass(frozen=True)
class EvidenceRead:
    context: object
    version: str
    family: str
    query: QuerySpec | None
    token: str | None
    origin: str
    wall_now: datetime
    target_id: UUID | None = None


@dataclass(frozen=True)
class EvidencePage:
    value: list[dict]
    next_link: str | None = None


async def snapshot_evidence(session, tenant_id, wall_now):
    state = await lock_state(session, tenant_id)
    settings = EvidenceSettings.model_validate(state.settings)
    when = settings.fixed_time if settings.clock_mode == "fixed" else wall_now
    return EvidenceSnapshot(
        state.generation,
        state.publication_revision,
        when,
        when - timedelta(days=settings.retention_days),
        wall_now + timedelta(hours=1),
    )


def bad_cursor():
    raise GraphError(
        400, "Request_UnsupportedQuery", "Evidence continuation is unavailable or changed"
    )


def binding(request, access, generation):
    c = request.context
    return dict(
        tenant=str(c.tenant_id),
        client=str(c.client_id),
        subject=str(c.subject_id),
        kind=c.token_kind,
        permissions=hashlib.sha256("\0".join(sorted(c.permissions)).encode()).hexdigest(),
        access=asdict(access) | {"subject_id": str(access.subject_id)},
        version=request.version,
        family=request.family,
        generation=str(generation),
    )


async def issue_cursor(session, snapshot, tenant_id, state, predecessor):
    message = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    raw = (
        hmac.digest(
            predecessor.encode("ascii"), b"mockazure-evidence-page-v1\0" + message, "sha256"
        )
        if predecessor
        else secrets.token_bytes(32)
    )
    token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    await session.execute(
        insert(EvidenceCursor)
        .values(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            tenant_id=tenant_id,
            **asdict(snapshot),
            state=state,
        )
        .on_conflict_do_nothing()
    )
    return token


async def read_evidence(session, request):
    access = await authorize_evidence(
        session,
        request.context,
        EvidenceOperation(request.version, request.family, target_id=request.target_id),
    )
    current = await lock_state(session, request.context.tenant_id)
    current_binding = binding(request, access, current.generation)
    last = None
    query = request.query
    if request.token:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", request.token):
            bad_cursor()
        row = await session.get(EvidenceCursor, hashlib.sha256(request.token.encode()).hexdigest())
        if (
            row is None
            or row.tenant_id != request.context.tenant_id
            or row.expires_at <= request.wall_now
            or row.state.get("binding") != current_binding
            or row.generation != current.generation
        ):
            bad_cursor()
        snapshot = EvidenceSnapshot(
            row.generation, row.revision, row.evidence_time, row.retention_cutoff, row.expires_at
        )
        query = decode_node(row.state["query"])
        last = row.state["last"]
    else:
        snapshot = await snapshot_evidence(session, request.context.tenant_id, request.wall_now)
    payload = disclosed_payload(request.family, access)
    conditions = [
        EvidenceVersion.tenant_id == request.context.tenant_id,
        EvidenceVersion.generation == snapshot.generation,
        EvidenceVersion.family == request.family,
        EvidenceVersion.valid_from <= snapshot.revision,
        or_(EvidenceVersion.valid_to.is_(None), EvidenceVersion.valid_to > snapshot.revision),
        EvidenceVersion.available_at <= snapshot.evidence_time,
        or_(
            EvidenceVersion.family == "riskyUser",
            EvidenceVersion.event_time >= snapshot.retention_cutoff,
        ),
        EvidenceVersion.event_time <= snapshot.evidence_time,
    ]
    if access.subject_id is not None:
        conditions.append(EvidenceVersion.subject_id == access.subject_id)
    if request.family == "signIn" and (
        request.version == "v1.0"
        or (request.target_id is None and not contains_event_type(query.filter))
    ):
        conditions.append(EvidenceVersion.payload["isInteractive"].as_boolean().is_(True))
    if request.target_id is not None:
        conditions.append(EvidenceVersion.record_id == request.target_id)
    if query.filter:
        conditions.append(
            compile_evidence_filter(
                query.filter, evidence_contract(request.version, request.family), payload
            )
        )
    time_order = query.order[0].field != "id"
    ascending = query.order[0].direction == "asc"
    if last:
        identifier = UUID(last[-1])
        if time_order:
            stamp = datetime.fromisoformat(last[0])
            conditions.append(
                or_(
                    EvidenceVersion.event_time > stamp
                    if ascending
                    else EvidenceVersion.event_time < stamp,
                    and_(
                        EvidenceVersion.event_time == stamp, EvidenceVersion.record_id > identifier
                    ),
                )
            )
        else:
            conditions.append(EvidenceVersion.record_id > identifier)
    ordering = [EvidenceVersion.record_id.asc()]
    if time_order:
        ordering.insert(
            0, EvidenceVersion.event_time.asc() if ascending else EvidenceVersion.event_time.desc()
        )
    rows = (
        await session.execute(
            select(
                EvidenceVersion.record_id,
                EvidenceVersion.event_time,
                payload.label("public_payload"),
            )
            .where(*conditions)
            .order_by(*ordering)
            .limit(query.top + 1)
        )
    ).all()
    result = [
        serialize_payload(request.version, request.family, row.public_payload, query.select)
        for row in rows[: query.top]
    ]
    link = None
    if len(rows) > query.top:
        row = rows[query.top - 1]
        last = (
            [row.event_time.isoformat(), str(row.record_id)] if time_order else [str(row.record_id)]
        )
        state = {"binding": current_binding, "query": encode_node(query), "last": last}
        token = await issue_cursor(
            session, snapshot, request.context.tenant_id, state, request.token
        )
        link = f"{request.origin}/{request.version}/{PATHS[request.family]}?" + urlencode(
            {"$skiptoken": token}
        )
    return EvidencePage(result, link)

"""Database materialized rows provide stable bounded-memory collection snapshots."""

import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy import delete, func, insert, literal, select

from mockazure.providers.graph.continuations import decode_node, encode_node
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import context_url
from mockazure.providers.graph.security.queries import contract_for, filtered, resolve
from mockazure.providers.graph.security.serialization import serialize_security
from mockazure.security.models import SecuritySnapshot, SecuritySnapshotItem
from mockazure.security.owners import owner_payloads
from mockazure.security.repository import document, security_source


def bad_cursor():
    raise GraphError(
        400, "Request_UnsupportedQuery", "Security continuation is unavailable or changed"
    )


def binding(request, actor, kind):
    return {
        "client": str(actor.client_id),
        "subject": str(actor.subject_id),
        "permissions": sorted(actor.permissions),
        "tokenKind": actor.token_kind,
        "resource": kind,
        "path": request.url.path,
    }


def token_for(snapshot, position):
    payload = f"{snapshot.id.hex}.{position}"
    digest = hmac.digest(snapshot.signing_secret.encode(), payload.encode(), "sha256").hex()
    return payload + "." + digest


async def security_collection(request, session, actor, kind, query, *, source=None):
    now = datetime.now(UTC)
    token = request.query_params.get("$skiptoken")
    position = 0
    if token is not None:
        if (
            set(request.query_params) != {"$skiptoken"}
            or len(request.query_params.multi_items()) != 1
        ):
            bad_cursor()
        if re.fullmatch(r"[0-9a-f]{32}\.[0-9]{1,16}\.[0-9a-f]{64}", token) is None:
            bad_cursor()
        identifier, offset, _ = token.split(".")
        snapshot = await session.get(SecuritySnapshot, (actor.tenant_id, UUID(identifier)))
        position = int(offset)
        if (
            snapshot is None
            or snapshot.expires_at <= now
            or snapshot.binding != binding(request, actor, kind)
            or not hmac.compare_digest(token, token_for(snapshot, position))
        ):
            bad_cursor()
        query = decode_node(snapshot.query)
    else:
        await session.execute(
            delete(SecuritySnapshot).where(
                SecuritySnapshot.tenant_id == actor.tenant_id, SecuritySnapshot.expires_at <= now
            )
        )
        snapshot = SecuritySnapshot(
            tenant_id=actor.tenant_id,
            binding=binding(request, actor, kind),
            query=encode_node(query),
            signing_secret=secrets.token_hex(32),
            expires_at=now + timedelta(hours=1),
        )
        session.add(snapshot)
        await session.flush()
        source = security_source(actor.tenant_id, kind) if source is None else source
        contract = contract_for(kind, request.url.path.split("/")[1])
        order = [
            getattr(resolve(source, contract.fields[term.field]), term.direction)()
            for term in query.order
        ]
        payload = source.c.payload
        for expansion in query.expand:
            payload = payload.op("||")(document(**{expansion.field: owner_payloads(source, actor)}))
        # SQL owns materialization; there is no all-rows Python list or giant JSON aggregate.
        await session.execute(
            insert(SecuritySnapshotItem).from_select(
                ["tenant_id", "snapshot_id", "position", "payload"],
                select(
                    literal(actor.tenant_id),
                    literal(snapshot.id),
                    func.row_number().over(order_by=order),
                    payload,
                ).where(filtered(source, query, contract)),
            )
        )
    size = min(query.top or 100, 999)
    rows = (
        (
            await session.execute(
                select(SecuritySnapshotItem)
                .where(
                    SecuritySnapshotItem.tenant_id == actor.tenant_id,
                    SecuritySnapshotItem.snapshot_id == snapshot.id,
                    SecuritySnapshotItem.position > position,
                )
                .order_by(SecuritySnapshotItem.position)
                .limit(size + 1)
            )
        )
        .scalars()
        .all()
    )
    value = []
    for row in rows[:size]:
        payload = row.payload
        value.append(serialize_security(payload, query))
    response = {
        "@odata.context": context_url(request, request.url.path.split("/")[-1]),
        "value": value,
    }
    if query.count:
        response["@odata.count"] = await session.scalar(
            select(func.count())
            .select_from(SecuritySnapshotItem)
            .where(
                SecuritySnapshotItem.tenant_id == actor.tenant_id,
                SecuritySnapshotItem.snapshot_id == snapshot.id,
            )
        )
    if len(rows) > size:
        origin = request.app.state.settings.advertised_endpoints(
            request.headers.get("host", "")
        ).graph
        response["@odata.nextLink"] = (
            origin
            + request.url.path
            + "?"
            + urlencode(
                {
                    "$skiptoken": token_for(snapshot, rows[size - 1].position),
                }
            )
        )
    return response

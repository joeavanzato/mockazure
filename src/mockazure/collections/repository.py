"""Atomic imports and SQL snapshot reads. No remote fetches or in-memory paging state."""

import hashlib
import hmac
import json
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from mockazure.authorization.roles import effective_roles
from mockazure.collections.contracts import properties, requirement, validate_records
from mockazure.collections.models import CollectionCursor, CollectionState
from mockazure.collections.models import CollectionRecord as R
from mockazure.collections.query import compiled, contract, order_field, ordering
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import permitted
from mockazure.providers.graph.query.parser import FilterParser, selection


async def import_records(session, tenant, family, records):
    validated = validate_records(family, records, tenant)
    await session.execute(
        insert(CollectionState).values(tenant_id=tenant, revision=0).on_conflict_do_nothing()
    )
    state = await session.scalar(
        select(CollectionState).where(CollectionState.tenant_id == tenant).with_for_update()
    )
    state.revision += 1
    revision = state.revision
    identifiers = [r["id"] for r, _ in validated]
    await session.execute(
        update(R)
        .where(
            R.tenant_id == tenant,
            R.family == family,
            R.record_id.in_(identifiers),
            R.valid_to.is_(None),
        )
        .values(valid_to=revision)
    )
    await session.execute(
        insert(R),
        [
            dict(
                tenant_id=tenant,
                family=family,
                record_id=r["id"],
                valid_from=revision,
                valid_to=None,
                payload=r,
                times=t,
            )
            for r, t in validated
        ],
    )
    await session.flush()
    return {"revision": revision, "count": len(validated)}


def bad_cursor():
    raise GraphError(
        400, "Request_UnsupportedQuery", "Collection continuation is unavailable or changed"
    )


def visible(tenant, family, revision):
    return [
        R.tenant_id == tenant,
        R.family == family,
        R.valid_from <= revision,
        or_(R.valid_to.is_(None), R.valid_to > revision),
    ]


async def binding(session, context, family, origin):
    roles = []
    if context.token_kind == "delegated":
        roles = sorted(await effective_roles(session, context.tenant_id, context.subject_id))
    return dict(
        tenant=str(context.tenant_id),
        client=str(context.client_id),
        subject=str(context.subject_id),
        kind=context.token_kind,
        permissions=sorted(context.permissions),
        roles=roles,
        family=family,
        origin=origin,
    )


async def read_records(session, context, family, values, token, origin, now, target=None):
    await permitted(context, requirement(family), session)
    bound = await binding(session, context, family, origin)
    last = None
    expires = now + timedelta(hours=1)
    if token is not None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            bad_cursor()
        row = await session.get(CollectionCursor, hashlib.sha256(token.encode()).hexdigest())
        if (
            row is None
            or row.tenant_id != context.tenant_id
            or row.expires_at <= now
            or row.state["binding"] != bound
        ):
            bad_cursor()
        revision = row.revision
        expires = row.expires_at
        values = row.state["query"]
        last = row.state["last"]
    else:
        revision = (
            await session.scalar(
                select(CollectionState.revision).where(
                    CollectionState.tenant_id == context.tenant_id
                )
            )
            or 0
        )
    conditions = visible(context.tenant_id, family, revision)
    if target is not None:
        conditions.append(R.record_id == target)
    if "$filter" in values:
        conditions.append(
            compiled(FilterParser(values["$filter"], max_bytes=16384).parse(), contract(family))
        )
    total = (
        await session.scalar(select(func.count()).select_from(R).where(*conditions))
        if values.get("$count") == "true"
        else None
    )
    field, direction = ordering(family, values)
    col = order_field(family, field)
    if last is not None:
        identifier, value = last
        if field == "id":
            conditions.append(
                R.record_id > identifier if direction == "asc" else R.record_id < identifier
            )
        elif value is None:
            conditions.append(and_(col.is_(None), R.record_id > identifier))
        else:
            conditions.append(
                or_(
                    col > value if direction == "asc" else col < value,
                    col.is_(None),
                    and_(col == value, R.record_id > identifier),
                )
            )
    top = min(int(values.get("$top", "100")), 1000)
    order = col.asc() if direction == "asc" else col.desc()
    query = (
        select(R.payload, col.label("sort_value"))
        .where(*conditions)
        .order_by(order.nulls_last(), R.record_id)
        .limit(top + 1)
    )
    if last is None:
        query = query.offset(int(values.get("$skip", "0")))
    rows = (await session.execute(query)).all()
    more = len(rows) > top
    rows = rows[:top]
    payloads = [dict(r.payload) for r in rows]
    if target is not None and not payloads:
        raise GraphError(404, "Request_ResourceNotFound", "Collection resource not found")
    if values.get("$expand") == "alerts":
        # Incident permission authorizes its alert relationship, per the incident operation.
        identifiers = [p["id"] for p in payloads]
        alerts = await session.scalars(
            select(R.payload)
            .where(
                *visible(context.tenant_id, "alerts_v2", revision),
                R.payload["incidentId"].as_string().in_(identifiers),
            )
            .order_by(R.record_id)
            .limit(10001)
        )
        alerts = list(alerts)
        if len(alerts) > 10000:
            raise GraphError(
                400,
                "Request_UnsupportedQuery",
                "Incident expansion exceeds 10000 alerts; query alerts_v2 separately",
            )
        grouped = {i: [] for i in identifiers}
        for alert in alerts:
            grouped[alert["incidentId"]].append(alert)
        for p in payloads:
            p["alerts"] = grouped[p["id"]]
    if "$select" in values:
        selected = set(selection(values["$select"], properties(family))) | {"id"}
        payloads = [{k: v for k, v in p.items() if k in selected} for p in payloads]
    if target is not None:
        return payloads[0]
    result = {"@odata.context": f"{origin}/v1.0/$metadata#security/{family}", "value": payloads}
    if total is not None:
        result["@odata.count"] = total
    if more:
        sort_value = rows[-1].sort_value
        if sort_value is not None and not isinstance(sort_value, (str, int, float, bool)):
            sort_value = float(sort_value)
        state = {"binding": bound, "query": values, "last": [rows[-1].payload["id"], sort_value]}
        message = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
        raw = hmac.digest(token.encode(), message, "sha256") if token else secrets.token_bytes(32)
        import base64

        successor = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        await session.execute(
            insert(CollectionCursor)
            .values(
                token_hash=hashlib.sha256(successor.encode()).hexdigest(),
                tenant_id=context.tenant_id,
                revision=revision,
                expires_at=expires,
                state=state,
            )
            .on_conflict_do_nothing()
        )
        result["@odata.nextLink"] = f"{origin}/v1.0/security/{family}?" + urlencode(
            {"$skiptoken": successor}
        )
    return result

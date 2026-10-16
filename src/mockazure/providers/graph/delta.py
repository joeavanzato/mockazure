"""Fixed revision directory delta rounds with bounded object/member pages."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

from fastapi import Request
from sqlalchemy import and_, exists, func, not_, or_, select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.orm import aliased

from mockazure.directory.history import current_revision, lock_history
from mockazure.directory.models import DirectoryEntry, ObjectVersion, RelationVersion
from mockazure.directory.types import PUBLIC_FIELDS, DirectoryObject
from mockazure.providers.graph.continuations import (
    CursorBinding,
    CursorState,
    MissingCursor,
    binding_for,
    issue_cursor,
    resolve_cursor,
)
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.permissions import (
    GraphOperation,
    authorize_directory,
    readable_fields,
)
from mockazure.providers.graph.probes import context_url, principal
from mockazure.providers.graph.query.ast import QuerySpec
from mockazure.providers.graph.query.contracts import unsupported
from mockazure.providers.graph.relationships import authorize_membership_visibility, visible_at
from mockazure.providers.graph.serialization import serialize_object


@dataclass(frozen=True)
class DeltaRequest:
    select: tuple[str, ...] | None
    token: str | None
    token_kind: str = "delta"
    origin: str = ""


@dataclass(frozen=True)
class DeltaPage:
    value: list[dict]
    next_link: str | None
    delta_link: str | None


def parse_delta(items: list[tuple[str, str]]) -> DeltaRequest:
    values = dict(items)
    if len(values) != len(items) or not values.keys() <= {"$select", "$skiptoken", "$deltatoken"}:
        unsupported("Delta accepts only select and its state links")
    if sum(len(k.encode()) + len(v.encode()) for k, v in items) > 16384:
        unsupported("Delta query exceeds its local size limit")
    if "$skiptoken" in values and "$deltatoken" in values:
        unsupported("Conflicting delta state tokens")
    token = values.get("$skiptoken", values.get("$deltatoken"))
    if token and len(values) != 1 or "$skiptoken" in values and not token:
        unsupported("Delta state links cannot change their query")
    chosen = tuple(values["$select"].split(",")) if "$select" in values else None
    if chosen is not None and (not all(chosen) or len(set(chosen)) != len(chosen)):
        unsupported("Invalid delta selection")
    return DeltaRequest(chosen, token or None, "page" if "$skiptoken" in values else "delta")


def link(request, binding, token, *, delta):
    return f"{request.origin}/{binding.version}/{binding.resource}?" + urlencode(
        {"$deltatoken" if delta else "$skiptoken": token}
    )


def resync(request, binding, fields):
    params = {"$deltatoken": ""}
    if fields is not None:
        params["$select"] = ",".join(fields)
    return f"{request.origin}/{binding.version}/{binding.resource}?" + urlencode(params)


def eligible_edges(tenant, lower, upper, group_id=None):
    conditions = [
        RelationVersion.tenant_id == tenant,
        RelationVersion.relation == "member",
        RelationVersion.valid_from <= upper,
    ]
    if group_id is not None:
        conditions.append(RelationVersion.group_id == group_id)
    if lower is None:
        conditions.extend(visible_at(RelationVersion, upper))
    else:
        removed_target = aliased(ObjectVersion)
        # Graph does not report implicit member removals caused by deleting the member object.
        implicit = exists(
            select(removed_target.object_id).where(
                removed_target.tenant_id == tenant,
                removed_target.object_id == RelationVersion.object_id,
                removed_target.removed.is_(True),
                removed_target.valid_from == RelationVersion.valid_to,
            )
        )
        conditions.extend(
            (
                or_(
                    RelationVersion.valid_from > lower,
                    and_(RelationVersion.valid_to > lower, RelationVersion.valid_to <= upper),
                ),
                not_(implicit),
            )
        )
    return conditions


async def member_chunk(session, context, group, lower, upper, after, limit):
    await authorize_membership_visibility(session, context, group, "members", upper)
    conditions = eligible_edges(context.tenant_id, lower, upper, group)
    if after:
        conditions.append(RelationVersion.object_id > UUID(after))
    active = func.bool_or(or_(RelationVersion.valid_to.is_(None), RelationVersion.valid_to > upper))
    rows = (
        await session.execute(
            select(
                RelationVersion.object_id,
                DirectoryEntry.kind,
                active.label("active"),
            )
            .join(
                DirectoryEntry,
                and_(
                    DirectoryEntry.tenant_id == RelationVersion.tenant_id,
                    DirectoryEntry.id == RelationVersion.object_id,
                ),
            )
            .where(*conditions)
            .group_by(RelationVersion.object_id, DirectoryEntry.kind)
            .order_by(RelationVersion.object_id)
            .limit(limit + 1)
        )
    ).all()
    value = []
    for identifier, kind, present in rows[:limit]:
        item = {"id": str(identifier), "@odata.type": f"#microsoft.graph.{kind}"}
        if not present:
            item["@removed"] = {"reason": "deleted"}
        value.append(item)
    return value, len(rows) > limit


async def read_delta(
    session, context, binding: CursorBinding, request: DeltaRequest, now: datetime
) -> DeltaPage:
    kind = binding.resource.split("/")[0][:-1]
    await authorize_directory(
        session, context, GraphOperation(binding.version, kind + "s.delta", "GET"), None
    )
    await lock_history(session, context.tenant_id)
    if request.token:
        try:
            state = await resolve_cursor(session, request.token, binding, now)
        except MissingCursor:
            error = GraphError(
                410, "syncStateNotFound", "Delta state is unavailable; resynchronize"
            )
            error.headers["Location"] = resync(request, binding, None)
            raise error from None
        except GraphError as error:
            if error.status == 410:
                error.headers["Location"] = resync(request, binding, error.cursor_query.select)
            raise
        if state.kind != request.token_kind:
            unsupported("Incorrect delta state parameter")
        if state.kind == "delta":
            state = replace(
                state,
                lower_revision=state.upper_revision,
                upper_revision=await current_revision(session, context.tenant_id),
                kind="page",
                last_key=None,
                expires_at=now + timedelta(days=7),
            )
    else:
        state = CursorState(
            binding,
            QuerySpec(select=request.select),
            None,
            await current_revision(session, context.tenant_id),
            None,
            "page",
            now + timedelta(days=7),
        )
    chosen = state.query.select
    allowed = PUBLIC_FIELDS[kind] | ({"members"} if kind == "group" else set())
    if chosen is not None and not set(chosen) <= allowed:
        unsupported("Unsupported delta property")
    visible = readable_fields(context, kind)
    if chosen is not None and not (set(chosen) - {"members"}) <= visible:
        raise GraphError(403, "Authorization_RequestDenied", "Delta properties are not readable")
    members = kind == "group" and (chosen is None or "members" in chosen)
    if members:
        await authorize_directory(
            session, context, GraphOperation(binding.version, "members", "GET"), None
        )
    fields = tuple(k for k in (chosen if chosen is not None else sorted(visible)) if k != "members")
    tracked = set(fields) | {
        "id"
    }  # Creation always participates, including members-only selection.
    conditions = [
        ObjectVersion.tenant_id == context.tenant_id,
        ObjectVersion.kind == kind,
        *visible_at(ObjectVersion, state.upper_revision),
    ]
    if state.lower_revision is None:
        conditions.append(ObjectVersion.removed.is_(False))
    else:
        changed = aliased(ObjectVersion)
        candidates = select(changed.object_id).where(
            changed.tenant_id == context.tenant_id,
            changed.kind == kind,
            changed.valid_from > state.lower_revision,
            changed.valid_from <= state.upper_revision,
            or_(changed.removed.is_(True), changed.changed_fields.has_any(array(sorted(tracked)))),
        )
        membership_candidates = select(RelationVersion.group_id).where(
            *eligible_edges(context.tenant_id, state.lower_revision, state.upper_revision)
        )
        conditions.append(
            or_(
                ObjectVersion.object_id.in_(candidates),
                ObjectVersion.object_id.in_(membership_candidates) if members else False,
            )
        )
    resume_group, member_after = state.last_key or (None, None)
    if resume_group:
        conditions.append(
            ObjectVersion.object_id >= UUID(resume_group)
            if member_after
            else ObjectVersion.object_id > UUID(resume_group)
        )
    objects = list(
        await session.scalars(
            select(ObjectVersion).where(*conditions).order_by(ObjectVersion.object_id).limit(101)
        )
    )
    value, remaining_members, more = [], 1000, len(objects) > 100
    last_key = state.last_key
    for row in objects[:100]:
        if row.removed:
            item = {"id": str(row.object_id), "@removed": {"reason": "deleted"}}
        else:
            item = serialize_object(
                DirectoryObject(kind, row.object_id, context.tenant_id, row.properties),
                fields,
                visible,
            )
            item["id"] = str(row.object_id)
            if members:
                edge_values, edge_more = await member_chunk(
                    session,
                    context,
                    row.object_id,
                    state.lower_revision,
                    state.upper_revision,
                    member_after if str(row.object_id) == resume_group else None,
                    remaining_members,
                )
                if edge_values:
                    item["members@delta"] = edge_values
                remaining_members -= len(edge_values)
                if edge_more:
                    value.append(item)
                    last_key = (str(row.object_id), edge_values[-1]["id"])
                    more = True
                    break
        value.append(item)
        last_key = (str(row.object_id), None)
        if remaining_members == 0:
            more = len(objects) > len(value)
            break
    if more:
        token = await issue_cursor(
            session, replace(state, last_key=last_key), predecessor=request.token
        )
        return DeltaPage(value, link(request, binding, token, delta=False), None)
    complete = replace(state, kind="delta", lower_revision=None, last_key=None)
    token = await issue_cursor(session, complete, predecessor=request.token)
    return DeltaPage(value, None, link(request, binding, token, delta=True))


def register_delta(router, version):
    @router.get("/users/delta()")
    @router.get("/users/microsoft.graph.delta()")
    @router.get("/groups/delta()")
    @router.get("/groups/microsoft.graph.delta()")
    @router.get("/users/delta")
    @router.get("/users/microsoft.graph.delta")
    @router.get("/groups/delta")
    @router.get("/groups/microsoft.graph.delta")
    async def delta(request: Request):
        from datetime import UTC

        resource = request.url.path.split("/")[2] + "/delta"
        query = parse_delta(list(request.query_params.multi_items()))
        query = replace(
            query,
            origin=request.app.state.settings.advertised_endpoints(
                request.headers.get("host", "")
            ).graph,
        )
        async with request.app.state.database.sessions.begin() as session:
            context = await principal(request, session)
            binding = await binding_for(session, context, version, resource)
            page = await read_delta(session, context, binding, query, datetime.now(UTC))
            result = {"@odata.context": context_url(request, resource), "value": page.value}
            result["@odata.nextLink" if page.next_link else "@odata.deltaLink"] = (
                page.next_link or page.delta_link
            )
            return result

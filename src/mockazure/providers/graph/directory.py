"""Graph directory HTTP contracts; domain writes are added separately."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import PlainTextResponse

from mockazure.directory.history import current_revision, lock_history
from mockazure.directory.models import Group
from mockazure.directory.repository import public_object, read_page
from mockazure.identity.models import User
from mockazure.providers.graph.continuations import (
    CursorState,
    binding_for,
    issue_cursor,
    resolve_cursor,
    state_link,
)
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.permissions import (
    GraphOperation,
    authorize_directory,
    readable_fields,
)
from mockazure.providers.graph.probes import context_url, principal
from mockazure.providers.graph.query.ast import Any, Boolean, Compare, In, StartsWith
from mockazure.providers.graph.query.contracts import contract_for, unsupported
from mockazure.providers.graph.query.parser import parse_query
from mockazure.providers.graph.relationships import relationship_page, relationship_values
from mockazure.providers.graph.serialization import serialize_object


def not_found():
    raise GraphError(404, "Request_ResourceNotFound", "Resource not found")


async def get_object(session, tenant_id, kind, value):
    model = User if kind == "user" else Group
    try:
        match = model.id == UUID(value)
    except ValueError:
        if kind != "user":
            not_found()
        match = User.upn == value.lower()
    row = await session.scalar(
        select(model).where(model.tenant_id == tenant_id, match, model.deleted_at.is_(None))
    )
    if row is None:
        not_found()
    return row


def filter_fields(node):
    if node is None:
        return set()
    if isinstance(node, Boolean):
        return set().union(*(filter_fields(child) for child in node.children))
    if isinstance(node, Compare):
        return {node.left.name}
    if isinstance(node, (In, StartsWith)):
        return {node.field.name}
    if isinstance(node, Any):
        return {node.field.name} | (filter_fields(node.body) - {node.variable})
    unsupported()


def authorized_query(request, context, version, kind):
    query = parse_query(
        list(request.query_params.multi_items()),
        contract_for(version, kind + "s"),
        request.headers.get("ConsistencyLevel"),
    )
    inferred_fields = filter_fields(query.filter) | {term.field for term in query.order}
    if not inferred_fields <= readable_fields(context, kind):
        raise GraphError(
            403,
            "Authorization_RequestDenied",
            "Query requires permission to read its filter and ordering fields",
        )
    return query


async def expand_members(session, context, version, object_id, query, data, snapshot=None):
    for expansion in query.expand:
        await authorize_directory(
            session, context, GraphOperation(version, "members", "GET"), object_id
        )
        data["members"] = await relationship_values(
            session,
            context,
            version,
            object_id,
            "members",
            expansion.select,
            limit=20,
            snapshot=snapshot,
        )


def router_for(version: str) -> APIRouter:
    if version not in {"v1.0", "beta"}:
        raise ValueError("Unsupported Graph version")
    router = APIRouter(prefix=f"/{version}")

    @router.get("/users/$count")
    @router.get("/groups/$count")
    async def count(request: Request):
        kind = "user" if request.url.path.split("/")[2] == "users" else "group"
        async with request.app.state.database.sessions.begin() as session:
            context = await principal(request, session)
            await authorize_directory(
                session, context, GraphOperation(version, kind + "s", "GET"), None
            )
            if request.headers.get("ConsistencyLevel") != "eventual":
                unsupported("Count segment requires ConsistencyLevel:eventual")
            items = list(request.query_params.multi_items())
            if any(key != "$filter" for key, _ in items):
                unsupported("Only filter is supported with the count segment")
            query = parse_query(
                items + [("$count", "true")], contract_for(version, kind + "s"), "eventual"
            )
            if not filter_fields(query.filter) <= readable_fields(context, kind):
                raise GraphError(
                    403, "Authorization_RequestDenied", "Filter fields are not readable"
                )
            await lock_history(session, context.tenant_id)
            snapshot = await current_revision(session, context.tenant_id)
            page = await read_page(session, context.tenant_id, kind, query, snapshot, None, 1)
            return PlainTextResponse(
                str(page.total), headers={"request-id": request.state.request_id}
            )

    @router.get("/users")
    @router.get("/groups")
    async def collection(request: Request):
        kind = "user" if request.url.path.endswith("/users") else "group"
        resource = kind + "s"
        async with request.app.state.database.sessions.begin() as session:
            context = await principal(request, session)
            await authorize_directory(
                session, context, GraphOperation(version, resource, "GET"), None
            )
            await lock_history(session, context.tenant_id)
            binding = await binding_for(session, context, version, resource)
            items = list(request.query_params.multi_items())
            now = datetime.now(UTC)
            predecessor = request.query_params.get("$skiptoken")
            if predecessor is not None:
                if len(items) != 1:
                    unsupported("Continuation requests cannot change query options")
                state = await resolve_cursor(session, predecessor, binding, now)
                if state.kind != "page":
                    unsupported("Expected a page continuation")
                query = state.query
            else:
                query = authorized_query(request, context, version, kind)
                snapshot = await current_revision(session, context.tenant_id)
                state = CursorState(
                    binding, query, None, snapshot, None, "page", now + timedelta(hours=1)
                )
            page = await read_page(
                session,
                context.tenant_id,
                kind,
                query,
                state.upper_revision,
                state.last_key,
                min(query.top or 100, 999),
            )
            value = []
            for obj in page.items:
                data = serialize_object(obj, query.select, readable_fields(context, kind, obj.id))
                await expand_members(
                    session, context, version, obj.id, query, data, state.upper_revision
                )
                value.append(data)
            result = {"@odata.context": context_url(request, resource), "value": value}
            if query.count:
                result["@odata.count"] = page.total
            if page.has_more:
                token = await issue_cursor(
                    session, replace(state, last_key=page.last_key), predecessor=predecessor
                )
                result["@odata.nextLink"] = state_link(request, version, resource, token)
            return result

    @router.get("/users/{object_id}")
    @router.get("/groups/{object_id}")
    async def entity(request: Request, object_id: str):
        kind = "user" if request.url.path.split("/")[2] == "users" else "group"
        async with request.app.state.database.sessions.begin() as session:
            context = await principal(request, session)
            target = None
            try:
                target = UUID(object_id)
            except ValueError:
                if context.subject_id is not None:
                    own = await session.get(User, context.subject_id)
                    if (
                        own is not None
                        and own.tenant_id == context.tenant_id
                        and own.upn == object_id.lower()
                    ):
                        target = own.id
            await authorize_directory(
                session, context, GraphOperation(version, kind + "s", "GET"), target
            )
            row = await get_object(session, context.tenant_id, kind, object_id)
            if any(
                key not in {"$select", "$expand"} for key, _ in request.query_params.multi_items()
            ):
                unsupported("Entity queries support only select and declared expansion")
            query = parse_query(
                list(request.query_params.multi_items()), contract_for(version, kind + "s"), None
            )
            await lock_history(session, context.tenant_id)
            data = serialize_object(
                public_object(row), query.select, readable_fields(context, kind, row.id)
            )
            await expand_members(session, context, version, row.id, query, data)
            return {"@odata.context": context_url(request, kind + "s/$entity"), **data}

    @router.get("/groups/{object_id}/members")
    @router.get("/groups/{object_id}/owners")
    @router.get("/groups/{object_id}/transitiveMembers")
    @router.get("/users/{object_id}/memberOf")
    @router.get("/users/{object_id}/transitiveMemberOf")
    async def relationship(request: Request, object_id: str):
        resource = request.url.path.rsplit("/", 1)[-1]
        upward = resource in {"memberOf", "transitiveMemberOf"}
        async with request.app.state.database.sessions.begin() as session:
            context = await principal(request, session)
            try:
                target = UUID(object_id)
            except ValueError:
                target = None
                if upward and context.subject_id is not None:
                    own = await session.get(User, context.subject_id)
                    if (
                        own is not None
                        and own.tenant_id == context.tenant_id
                        and own.upn == object_id.lower()
                    ):
                        target = own.id
            await authorize_directory(
                session, context, GraphOperation(version, resource, "GET"), target
            )
            parent = await get_object(
                session, context.tenant_id, "user" if upward else "group", object_id
            )
            if (
                not upward
                and parent.profile.get("visibility") == "HiddenMembership"
                and "Member.Read.Hidden" not in context.permissions
            ):
                raise GraphError(
                    403, "Authorization_RequestDenied", "Hidden membership permission is required"
                )

            await lock_history(session, context.tenant_id)
            route = f"{'users' if upward else 'groups'}/{parent.id}/{resource}"
            binding = await binding_for(session, context, version, route)
            items = list(request.query_params.multi_items())
            predecessor = request.query_params.get("$skiptoken")
            now = datetime.now(UTC)
            if predecessor is not None:
                if len(items) != 1:
                    unsupported("Continuation requests cannot change query options")
                state = await resolve_cursor(session, predecessor, binding, now)
                if state.kind != "page":
                    unsupported("Expected relationship page")
            else:
                if any(key not in {"$select", "$top"} for key, _ in items):
                    unsupported("Relationships support select, top and continuation")
                # Relationship values may mix user/group types. Validate selection independently
                # of whether the current result happens to be empty.
                from mockazure.directory.types import GROUP_FIELDS, USER_FIELDS
                from mockazure.providers.graph.query.ast import QuerySpec

                if len(dict(items)) != len(items):
                    unsupported("Duplicate query options")
                selected = request.query_params.get("$select")
                fields = tuple(selected.split(",")) if selected is not None else None
                if fields is not None and (
                    not set(fields) <= GROUP_FIELDS | USER_FIELDS | {"appId"}
                    or len(set(fields)) != len(fields)
                ):
                    unsupported("Invalid relationship selection")
                top = request.query_params.get("$top", "100")
                if not top.isascii() or not top.isdecimal() or len(top) > 6 or int(top) < 1:
                    unsupported("Invalid relationship page size")
                query = QuerySpec(select=fields, top=min(int(top), 999))
                state = CursorState(
                    binding,
                    query,
                    None,
                    await current_revision(session, context.tenant_id),
                    None,
                    "page",
                    now + timedelta(hours=1),
                )
            page = await relationship_page(
                session,
                context,
                version,
                parent.id,
                resource,
                state.query.select,
                state.upper_revision,
                state.last_key[0] if state.last_key else None,
                state.query.top or 100,
            )
            result = {"@odata.context": context_url(request, route), "value": page.value}
            if page.has_more:
                token = await issue_cursor(
                    session, replace(state, last_key=(page.last_id,)), predecessor=predecessor
                )
                result["@odata.nextLink"] = state_link(request, version, route, token)
            return result

    from mockazure.providers.graph.writes import register_writes

    register_writes(router, version)
    return router

"""Graph directory HTTP contracts; domain writes are added separately."""

from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import PlainTextResponse

from mockazure.directory.history import current_revision
from mockazure.directory.models import Group
from mockazure.directory.repository import public_object, read_page
from mockazure.identity.models import User
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
from mockazure.providers.graph.relationships import relationship_values
from mockazure.providers.graph.serialization import selected_fields, serialize_object


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


async def expand_members(session, context, version, object_id, query, data):
    for expansion in query.expand:
        await authorize_directory(
            session, context, GraphOperation(version, "members", "GET"), object_id
        )
        data["members"] = await relationship_values(
            session, context, version, object_id, "members", expansion.select, limit=20
        )


def router_for(version: str) -> APIRouter:
    if version not in {"v1.0", "beta"}:
        raise ValueError("Unsupported Graph version")
    router = APIRouter(prefix=f"/{version}")

    @router.get("/users/$count")
    @router.get("/groups/$count")
    async def count(request: Request):
        kind = "user" if request.url.path.split("/")[2] == "users" else "group"
        async with request.app.state.database.sessions() as session:
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
        async with request.app.state.database.sessions() as session:
            context = await principal(request, session)
            await authorize_directory(
                session, context, GraphOperation(version, resource, "GET"), None
            )
            query = authorized_query(request, context, version, kind)
            snapshot = await current_revision(session, context.tenant_id)
            page = await read_page(
                session, context.tenant_id, kind, query, snapshot, None, min(query.top or 100, 999)
            )
            if page.has_more:
                unsupported("Paged collection support is pending")
            value = []
            for obj in page.items:
                data = serialize_object(obj, query.select, readable_fields(context, kind, obj.id))
                await expand_members(session, context, version, obj.id, query, data)
                value.append(data)
            result = {"@odata.context": context_url(request, resource), "value": value}
            if query.count:
                result["@odata.count"] = page.total
            return result

    @router.get("/users/{object_id}")
    @router.get("/groups/{object_id}")
    async def entity(request: Request, object_id: str):
        kind = "user" if request.url.path.split("/")[2] == "users" else "group"
        async with request.app.state.database.sessions() as session:
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
        async with request.app.state.database.sessions() as session:
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
            result = await relationship_values(
                session, context, version, parent.id, resource, selected_fields(request)
            )
            return {
                "@odata.context": context_url(
                    request, f"{'users' if upward else 'groups'}/{parent.id}/{resource}"
                ),
                "value": result,
            }

    return router

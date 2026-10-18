"""Generic directory resolution over canonical, tenant-scoped public projections."""

from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.directory.models import Group
from mockazure.directory.repository import public_object
from mockazure.identity.models import User
from mockazure.providers.graph.body import invalid, read_json
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.permissions import readable_fields
from mockazure.providers.graph.probes import context_url, permitted, principal, project
from mockazure.providers.graph.security.serialization import serialize_security
from mockazure.providers.graph.serialization import serialize_object
from mockazure.security.repository import ensure_resource_principals, security_source

READ = OperationRequirement(
    "graph", frozenset({"Directory.Read.All"}), frozenset({"Directory.Read.All"})
)
KINDS = ("user", "group", "application", "servicePrincipal", "device")


async def resolve_objects(session, context, ids: list[str], types: list[str] | None) -> list[dict]:
    if not isinstance(ids, list) or not 1 <= len(ids) <= 1000:
        invalid("Supply between 1 and 1000 object IDs")
    try:
        identifiers = list(dict.fromkeys(UUID(value) for value in ids if isinstance(value, str)))
    except ValueError:
        invalid("Object IDs must be GUID strings")
    if any(not isinstance(value, str) for value in ids):
        invalid("Object IDs must be GUID strings")
    names = {kind.lower(): kind for kind in KINDS} | {"directoryobject": "directoryObject"}
    if types is not None and (
        not isinstance(types, list)
        or not types
        or len(types) > 32
        or any(not isinstance(value, str) or value.lower() not in names for value in types)
    ):
        invalid("Unsupported directory object types")
    kinds = {names[value.lower()] for value in types} if types else set(KINDS)
    if "directoryObject" in kinds:
        kinds = set(KINDS)
    result = {}
    for kind, model in (("user", User), ("group", Group)):
        if kind not in kinds:
            continue
        rows = await session.scalars(
            select(model).where(
                model.tenant_id == context.tenant_id,
                model.id.in_(identifiers),
                model.deleted_at.is_(None),
            )
        )
        for row in rows:
            data = serialize_object(
                public_object(row), None, readable_fields(context, kind, row.id)
            )
            result[row.id] = data | {"@odata.type": "#microsoft.graph." + kind}
    for kind in ("application", "servicePrincipal", "device"):
        if kind not in kinds:
            continue
        if kind == "servicePrincipal":
            await ensure_resource_principals(session, context.tenant_id)
        source = security_source(context.tenant_id, kind)
        for payload in await session.scalars(
            select(source.c.payload).where(source.c.id.in_(identifiers))
        ):
            result[UUID(payload["id"])] = serialize_security(payload)
    return [result[identifier] for identifier in identifiers if identifier in result]


def router() -> APIRouter:
    routes = APIRouter(prefix="/v1.0")

    @routes.post("/directoryObjects/getByIds")
    @routes.post("/directoryObjects/microsoft.graph.getByIds")
    async def by_ids(request: Request):
        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            await permitted(actor, READ, session)
            if request.query_params:
                raise GraphError(400, "Request_UnsupportedQuery", "Query options are unsupported")
            body = await read_json(request)
            if set(body) - {"ids", "types"}:
                invalid("Unsupported request property")
            values = await resolve_objects(session, actor, body.get("ids"), body.get("types"))
            return {"@odata.context": context_url(request, "directoryObjects"), "value": values}

    @routes.get("/directoryObjects/{identifier}")
    async def item(identifier: str, request: Request):
        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            await permitted(actor, READ, session)
            values = await resolve_objects(session, actor, [identifier], None)
            if not values:
                raise GraphError(404, "Request_ResourceNotFound", "Resource not found")
            value = project(request, values[0])
            value["@odata.type"] = values[0]["@odata.type"]
            return {"@odata.context": context_url(request, "directoryObjects/$entity"), **value}

    @routes.get("/users/{identifier}/ownedObjects")
    @routes.get("/me/ownedObjects")
    async def owned(request: Request, identifier: str | None = None):
        from mockazure.providers.graph.directory import get_object
        from mockazure.providers.graph.security.queries import parse_security_query
        from mockazure.security.collection_relationships import owned_source
        from mockazure.security.paging import security_collection

        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            if request.url.path.startswith("/v1.0/me/"):
                if actor.token_kind != "delegated":
                    raise GraphError(400, "BadRequest", "/me requires delegated authentication")
                identifier = str(actor.subject_id)
            names = {
                "User.Read.All",
                "User.ReadWrite.All",
                "Directory.Read.All",
                "Directory.ReadWrite.All",
            }
            # Authorize before resolving another user's existence.
            self_user = await session.get(User, actor.subject_id) if actor.subject_id else None
            self_access = self_user is not None and identifier.lower() in {
                str(self_user.id),
                self_user.upn.lower(),
            }
            await permitted(
                actor,
                OperationRequirement(
                    "graph",
                    frozenset(),
                    frozenset(names | ({"User.Read"} if self_access else set())),
                ),
                session,
            )
            user = await get_object(session, actor.tenant_id, "user", identifier)
            if set(request.query_params) - {"$select", "$top", "$skiptoken"}:
                raise GraphError(400, "Request_UnsupportedQuery", "Unsupported ownedObjects query")
            query = (
                None
                if "$skiptoken" in request.query_params
                else parse_security_query(request, "ownedObject")
            )
            return await security_collection(
                request, session, actor, "ownedObject", query, source=owned_source(actor, user.id)
            )

    return routes

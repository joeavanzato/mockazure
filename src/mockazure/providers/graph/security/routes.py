from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Request, Response
from sqlalchemy.exc import IntegrityError

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.providers.graph.body import read_json, typed_body
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import context_url, permitted, principal, project
from mockazure.providers.graph.security.queries import parse_security_query
from mockazure.providers.graph.security.serialization import disclose_owners, serialize_security
from mockazure.security.mutations import mutate_security, owner_list
from mockazure.security.paging import security_collection
from mockazure.security.repository import (
    ensure_resource_principals,
    lock_security,
    public_security_object,
)


def read_requirement(kind):
    names = (
        {"Device.Read.All", "Device.ReadWrite.All", "Directory.Read.All", "Directory.ReadWrite.All"}
        if kind == "device"
        else {
            "Application.Read.All",
            "Application.ReadWrite.All",
            "Directory.Read.All",
            "Directory.ReadWrite.All",
        }
    )
    application_names = names | ({"Application.ReadWrite.OwnedBy"} if kind != "device" else set())
    return OperationRequirement("graph", frozenset(application_names), frozenset(names))


def router_for(version):
    router = APIRouter(prefix="/" + version)

    def register(resource, kind):
        async def collection(request: Request):
            async with request.app.state.database.sessions.begin() as session:
                actor = await principal(request, session)
                await permitted(actor, read_requirement(kind), session)
                await lock_security(session, actor.tenant_id)
                if kind == "servicePrincipal":
                    await ensure_resource_principals(session, actor.tenant_id)
                query = (
                    None
                    if "$skiptoken" in request.query_params
                    else parse_security_query(request, kind)
                )
                return await security_collection(request, session, actor, kind, query)

        async def item(identifier: str, request: Request):
            return await get_item(identifier, request)

        async def alternate(identifier: str, request: Request):
            return await get_item(identifier, request, by_appid=True)

        async def get_item(identifier, request, by_appid=False):
            async with request.app.state.database.sessions.begin() as session:
                actor = await principal(request, session)
                await permitted(actor, read_requirement(kind), session)
                await lock_security(session, actor.tenant_id)
                if kind == "servicePrincipal":
                    await ensure_resource_principals(session, actor.tenant_id)
                data = await public_security_object(
                    session, actor.tenant_id, kind, identifier, by_appid=by_appid
                )
                if set(request.query_params) - {"$select", "$expand"}:
                    raise GraphError(400, "Request_UnsupportedQuery", "Item query is unsupported")
                query = parse_security_query(request, kind)
                for expansion in query.expand:
                    data[expansion.field] = disclose_owners(
                        await owner_list(session, actor.tenant_id, UUID(data["id"])), actor
                    )
                return {
                    "@odata.context": context_url(request, resource + "/$entity"),
                    **serialize_security(data, query),
                }

        async def write(request: Request, identifier=None, operation="create", body=None):
            if request.query_params:
                raise GraphError(400, "Request_UnsupportedQuery", "Write query is unsupported")
            if body is None:
                body = {} if request.method == "DELETE" else await read_json(request)
            if operation in {"create", "update"}:
                body = typed_body(body, kind)
            try:
                async with request.app.state.database.sessions.begin() as session:
                    actor = await principal(request, session)
                    if identifier and request.url.path.split("/")[2].startswith(resource + "("):
                        obj = await public_security_object(
                            session, actor.tenant_id, kind, identifier, by_appid=True
                        )
                        identifier = obj["id"]
                    result = await mutate_security(
                        session, actor, kind, operation, identifier, body, datetime.now(UTC)
                    )
            except IntegrityError:
                raise GraphError(
                    409, "Request_Conflict", "Security object conflicts with existing state"
                ) from None
            if result is None:
                return Response(status_code=204)
            from starlette.responses import JSONResponse

            return JSONResponse(result, status_code=201 if operation == "create" else 200)

        async def create(request: Request):
            return await write(request)

        async def update(identifier: str, request: Request):
            return await write(request, identifier, "update")

        async def remove(identifier: str, request: Request):
            return await write(request, identifier, "delete")

        async def add_secret(identifier: str, request: Request):
            return await write(request, identifier, "add_password")

        async def remove_secret(identifier: str, request: Request):
            return await write(request, identifier, "remove_password")

        async def owners(identifier: str, request: Request):
            async with request.app.state.database.sessions.begin() as session:
                actor = await principal(request, session)
                await permitted(actor, read_requirement(kind), session)
                await lock_security(session, actor.tenant_id)
                obj = await public_security_object(
                    session,
                    actor.tenant_id,
                    kind,
                    identifier,
                    by_appid=request.url.path.split("/")[2].startswith(resource + "("),
                )
                values = await owner_list(session, actor.tenant_id, UUID(obj["id"]))
                values = disclose_owners(values, actor)
                return {
                    "@odata.context": context_url(request, resource + "/" + relation),
                    "value": [project(request, value) for value in values],
                }

        async def add_owner(identifier: str, request: Request):
            body = await read_json(request)
            if set(body) != {"@odata.id"} or not isinstance(body["@odata.id"], str):
                raise GraphError(400, "Request_BadRequest", "An owner reference is required")
            parts = urlsplit(body["@odata.id"])
            allowed = set(request.app.state.settings.allowed_hosts) | {"graph.microsoft.com"}
            segments = parts.path.strip("/").split("/")
            if (
                parts.scheme != "https"
                or parts.hostname not in allowed
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
                or len(segments) != 3
                or segments[0] not in {"v1.0", "beta"}
                or segments[1] not in {"directoryObjects", "users", "servicePrincipals"}
            ):
                raise GraphError(
                    400, "Request_BadRequest", "Owner reference is outside the local namespace"
                )
            return await write(request, identifier, "add_owner", {"owner_id": segments[2]})

        async def remove_owner(identifier: str, owner: str, request: Request):
            return await write(request, identifier, "remove_owner", {"owner_id": owner})

        router.add_api_route("/" + resource, create, methods=["POST"])
        router.add_api_route("/" + resource, collection, methods=["GET"])
        relation = "registeredOwners" if kind == "device" else "owners"
        key = "deviceId" if kind == "device" else "appId"
        for prefix, reader in [
            ("/" + resource + "/{identifier}", item),
            ("/" + resource + "(" + key + "='{identifier}')", alternate),
        ]:
            router.add_api_route(prefix, reader, methods=["GET"])
            router.add_api_route(prefix, update, methods=["PATCH"])
            router.add_api_route(prefix, remove, methods=["DELETE"])
            router.add_api_route(prefix + "/" + relation, owners, methods=["GET"])
            router.add_api_route(prefix + "/" + relation + "/$ref", add_owner, methods=["POST"])
            router.add_api_route(
                prefix + "/" + relation + "/{owner}/$ref", remove_owner, methods=["DELETE"]
            )
            if kind != "device":
                for namespace in ("", "microsoft.graph."):
                    router.add_api_route(
                        prefix + "/" + namespace + "addPassword", add_secret, methods=["POST"]
                    )
                    router.add_api_route(
                        prefix + "/" + namespace + "removePassword", remove_secret, methods=["POST"]
                    )

    for resource, kind in [
        ("applications", "application"),
        ("servicePrincipals", "servicePrincipal"),
        ("devices", "device"),
    ]:
        register(resource, kind)
    return router

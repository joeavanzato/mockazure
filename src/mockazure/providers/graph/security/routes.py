from fastapi import APIRouter, Request

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.providers.graph.probes import context_url, permitted, principal, project
from mockazure.providers.graph.security.queries import parse_security_query
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
                return {
                    "@odata.context": context_url(request, resource + "/$entity"),
                    **project(request, data),
                }

        router.add_api_route("/" + resource, collection, methods=["GET"])
        if kind != "device":
            router.add_api_route(
                "/" + resource + "(appId='{identifier}')", alternate, methods=["GET"]
            )
        router.add_api_route("/" + resource + "/{identifier}", item, methods=["GET"])

    for resource, kind in [
        ("applications", "application"),
        ("servicePrincipals", "servicePrincipal"),
        ("devices", "device"),
    ]:
        register(resource, kind)
    return router

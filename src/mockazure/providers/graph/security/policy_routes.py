"""Permission- and licence-checked Conditional Access resource operations."""

from fastapi import APIRouter, Request, Response
from starlette.responses import JSONResponse

from mockazure.providers.graph.body import read_json, typed_body
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import principal
from mockazure.providers.graph.security.queries import parse_security_query
from mockazure.providers.graph.security.serialization import serialize_security
from mockazure.security.paging import security_collection
from mockazure.security.policies import mutate_policy, require_policy_access
from mockazure.security.repository import lock_security, public_security_object


def router_for(version):
    router = APIRouter(prefix="/" + version + "/identity/conditionalAccess")

    def route(path, kind, method, item=False):
        async def handle(request: Request):
            async with request.app.state.database.sessions.begin() as session:
                actor = await principal(request, session)
                await require_policy_access(session, actor, kind, method)
                await lock_security(session, actor.tenant_id)
                identifier = request.path_params.get("identifier")
                if method == "GET":
                    if item:
                        if set(request.query_params) - {"$select"}:
                            raise GraphError(
                                400, "Request_UnsupportedQuery", "Only item selection is supported"
                            )
                        value = await public_security_object(
                            session, actor.tenant_id, kind, identifier
                        )
                        return serialize_security(value, parse_security_query(request, kind))
                    query = (
                        None
                        if "$skiptoken" in request.query_params
                        else parse_security_query(request, kind)
                    )
                    return await security_collection(request, session, actor, kind, query)
                if request.query_params:
                    raise GraphError(
                        400, "Request_UnsupportedQuery", "Mutation query options are unsupported"
                    )
                body = await read_json(request) if method != "DELETE" else None
                if body is not None and kind == "conditionalAccessPolicy":
                    body = typed_body(body, kind)
                value = await mutate_policy(session, actor, kind, method, identifier, body)
                if method == "POST":
                    return JSONResponse(value, status_code=201)
                return Response(status_code=204)

        router.add_api_route(path + ("/{identifier}" if item else ""), handle, methods=[method])

    for path, kind in (
        ("/policies", "conditionalAccessPolicy"),
        ("/namedLocations", "ipNamedLocation"),
    ):
        for method, item in (
            ("GET", False),
            ("GET", True),
            ("POST", False),
            ("PATCH", True),
            ("DELETE", True),
        ):
            route(path, kind, method, item)
    return router

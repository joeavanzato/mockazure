from fastapi import APIRouter, Request, Response

from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import principal
from mockazure.security.method_policies import (
    configurations,
    identifier,
    policy_document,
    require_policy_access,
    update_configuration,
)
from mockazure.security.repository import lock_security


def router_for():
    router = APIRouter(prefix="/v1.0/policies/authenticationMethodsPolicy")

    async def handle(request: Request):
        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            await lock_security(session, actor.tenant_id)
            await require_policy_access(
                session,
                actor,
                write=request.method == "PATCH",
                root=request.url.path.endswith("authenticationMethodsPolicy"),
            )
            if request.query_params:
                raise GraphError(400, "Request_UnsupportedQuery", "Query options are unsupported")
            name = request.path_params.get("configuration")
            if name:
                name = identifier(name)
            if request.method == "PATCH":
                if not name:
                    raise GraphError(501, "NotSupported", "Root policy writes are unsupported")
                await update_configuration(
                    session, actor, name, await read_json(request, limit=65536)
                )
                return Response(status_code=204)
            if name:
                return next(
                    v for v in await configurations(session, actor.tenant_id) if v["id"] == name
                )
            if request.url.path.endswith("authenticationMethodConfigurations"):
                return {"value": await configurations(session, actor.tenant_id)}
            return await policy_document(session, actor.tenant_id)

    for path in (
        "",
        "/authenticationMethodConfigurations",
        "/authenticationMethodConfigurations/{configuration}",
    ):
        router.add_api_route(path, handle, methods=["GET"])
    for path in ("", "/authenticationMethodConfigurations/{configuration}"):
        router.add_api_route(path, handle, methods=["PATCH"])
    return router

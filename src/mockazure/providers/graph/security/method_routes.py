"""Graph v1.0 method inventory backed by canonical users and lab enrollments."""

from fastapi import APIRouter, Request

from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import context_url, principal
from mockazure.security.methods import METHODS, method_user, require_method_read, stored_methods
from mockazure.security.repository import missing


def router_for(version):
    router = APIRouter(prefix="/" + version)

    def route(collection, self_path, item):
        async def read(request: Request):
            async with request.app.state.database.sessions() as session:
                actor = await principal(request, session)
                if self_path and actor.token_kind != "delegated":
                    raise GraphError(400, "BadRequest", "/me requires delegated authentication")
                user = await method_user(
                    session,
                    actor.tenant_id,
                    actor.subject_id if self_path else request.path_params["user_id"],
                )
                mask = await require_method_read(session, actor, user, collection)
                if request.query_params:
                    raise GraphError(
                        400, "Request_UnsupportedQuery", "Method query options are unsupported"
                    )
                kind = None if collection == "methods" else METHODS[collection][0]
                values = await stored_methods(session, actor.tenant_id, user, kind)
                if mask:
                    for value in values:
                        if value.get("phoneNumber"):
                            phone = value["phoneNumber"]
                            value["phoneNumber"] = "*" * max(0, len(phone) - 4) + phone[-4:]
                if item:
                    identifier = request.path_params["identifier"]
                    for value in values:
                        if value["id"] == identifier:
                            return value
                    missing()
                return {
                    "@odata.context": context_url(
                        request, f"users('{user.id}')/authentication/{collection}"
                    ),
                    "value": values,
                }

        prefix = "/me" if self_path else "/users/{user_id}"
        path = prefix + "/authentication/" + collection + ("/{identifier}" if item else "")
        router.add_api_route(path, read, methods=["GET"])

    for collection in ("methods", *METHODS):
        for self_path in (False, True):
            for item in (False, True):
                route(collection, self_path, item)
    return router

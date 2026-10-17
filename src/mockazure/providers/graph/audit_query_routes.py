"""Graph v1.0 UAL operation adapters; beta exposes the same bounded profile."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mockazure.auditlog.paging import options, read_page, selected
from mockazure.auditlog.queries import create_query, get_query, query_view
from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import principal


def router_for(version):
    router = APIRouter(prefix="/" + version + "/security/auditLog/queries")

    def register(suffix, method):
        async def handle(request: Request):
            try:
                async with request.app.state.database.sessions.begin() as session:
                    actor = await principal(request, session)
                    now = getattr(request.app.state, "clock", lambda: datetime.now(UTC))()
                    identifier = request.path_params.get("query_id")
                    if method == "POST":
                        if request.query_params:
                            raise GraphError(
                                400,
                                "Request_UnsupportedQuery",
                                "Write query options are unsupported",
                            )
                        value = await create_query(session, actor, await read_json(request), now)
                        return JSONResponse(value, status_code=201)
                    if suffix == "/{query_id}":
                        row, job = await get_query(session, actor, identifier, now)
                        values = list(request.query_params.multi_items())
                        if any(key != "$select" for key, _ in values):
                            raise GraphError(
                                400, "Request_UnsupportedQuery", "Unsupported audit query option"
                            )
                        query = options(values, False)
                        return selected(query_view(row, job), query.select)
                    return await read_page(session, actor, request, version, now, identifier)
            except AuthorizationDenied:
                raise GraphError(
                    403, "Authorization_RequestDenied", "Audit query access is not permitted"
                ) from None

        router.add_api_route(suffix, handle, methods=[method])

    register("", "POST")
    register("", "GET")
    register("/{query_id}", "GET")
    register("/{query_id}/records", "GET")
    return router

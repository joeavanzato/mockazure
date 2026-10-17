"""Explicit resource-group and scope-tag write routes."""

from fastapi import APIRouter, Request, Response
from starlette.responses import JSONResponse

from mockazure.providers.arm.authorization import require_action
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import resolve_scope
from mockazure.providers.arm.inventory_routes import request_scope
from mockazure.providers.arm.mutations import body, group_write, tag_view, tags_write
from mockazure.providers.arm.paging import parameters
from mockazure.providers.arm.probes import principal
from mockazure.providers.arm.scopes import parse_scope

router = APIRouter()


@router.api_route("/subscriptions/{subscription}/resourcegroups/{group}", methods=["PUT", "PATCH"])
async def groups_write(subscription: str, group: str, request: Request):
    parameters(request, "groups.put" if request.method == "PUT" else "groups.patch")
    scope = request_scope(subscription, group)
    result, status = await group_write(
        request, scope, await body(request), partial=request.method == "PATCH"
    )
    return JSONResponse(result, status_code=status)


@router.api_route(
    "/{scope_path:path}/providers/microsoft.resources/tags/default",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def tags(scope_path: str, request: Request):
    parameters(request, "tags." + request.method.lower())
    try:
        scope = parse_scope("/" + scope_path)
    except ValueError:
        raise ArmError(400, "InvalidResourceId", "Invalid tag scope") from None
    if request.method == "GET":
        async with request.app.state.database.sessions() as session:
            actor = await principal(request, session)
            await require_action(session, actor, scope, "Microsoft.Resources/tags/read")
            row = await resolve_scope(session, actor.tenant_id, scope)
            return tag_view(scope, row.tags)
    result = await tags_write(
        request, scope, {} if request.method == "DELETE" else await body(request)
    )
    return Response(status_code=204) if request.method == "DELETE" else result

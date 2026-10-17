"""Explicit public role-definition and role-assignment routes."""

from uuid import UUID

from fastapi import APIRouter, Request, Response
from starlette.responses import JSONResponse

from mockazure.providers.arm.authorization import require_action
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.mutations import body
from mockazure.providers.arm.probes import principal
from mockazure.providers.arm.rbac import (
    AUTH,
    assignment_get,
    assignment_mutate,
    assignments_list,
    definitions,
    rbac_parameters,
)
from mockazure.providers.arm.scopes import parse_scope

router = APIRouter()


def scope_from(path):
    try:
        return parse_scope("/" + path)
    except ValueError:
        raise ArmError(400, "InvalidResourceId", "Invalid RBAC scope") from None


def identifier_from(value):
    try:
        return UUID(value)
    except ValueError:
        raise ArmError(400, "InvalidRoleAssignmentId", "Invalid assignment ID") from None


@router.get("/{scope_path:path}/providers/microsoft.authorization/roledefinitions")
@router.get("/{scope_path:path}/providers/microsoft.authorization/roledefinitions/{identifier}")
async def roles(scope_path: str, request: Request, identifier: str | None = None):
    rbac_parameters(
        request, "definitions.get" if identifier else "definitions.list", listing=identifier is None
    )
    if "$skipToken" in request.query_params:
        raise ArmError(400, "InvalidSkipToken", "Built-in definitions fit in one page")
    scope = scope_from(scope_path)
    async with request.app.state.database.sessions() as session:
        actor = await principal(request, session)
        await require_action(session, actor, scope, AUTH + "roleDefinitions/read")
        return definitions(scope, identifier, request.query_params.get("$filter"))


@router.get("/{scope_path:path}/providers/microsoft.authorization/roleassignments")
async def assignment_list(scope_path: str, request: Request):
    rbac_parameters(request, "assignments.list", listing=True)
    scope = scope_from(scope_path)
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        return await assignments_list(request, session, actor, scope)


@router.api_route(
    "/{scope_path:path}/providers/microsoft.authorization/roleassignments/{identifier}",
    methods=["GET", "PUT", "DELETE"],
)
async def assignments(scope_path: str, identifier: str, request: Request):
    rbac_parameters(request, "assignments." + request.method.lower())
    scope, assignment_id = scope_from(scope_path), identifier_from(identifier)
    if request.method == "GET":
        async with request.app.state.database.sessions() as session:
            actor = await principal(request, session)
            return await assignment_get(session, actor, scope, assignment_id)
    result, status = await assignment_mutate(
        request, scope, assignment_id, {} if request.method == "DELETE" else await body(request)
    )
    return Response(status_code=204) if status == 204 else JSONResponse(result, status_code=status)

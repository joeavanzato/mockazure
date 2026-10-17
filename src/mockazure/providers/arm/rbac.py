"""Public ARM RBAC uses the same canonical assignments as local administration."""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select

from mockazure.directory.models import Group
from mockazure.identity.models import ServicePrincipal, User
from mockazure.providers.arm.assignments import assignment_view, set_assignment
from mockazure.providers.arm.authorization import require_action
from mockazure.providers.arm.contracts import (
    builtin_roles,
    operation_contract,
    validate_arm_payload,
)
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.facts import append_arm_fact
from mockazure.providers.arm.inventory import resolve_scope
from mockazure.providers.arm.models import RoleAssignment
from mockazure.providers.arm.mutations import transaction
from mockazure.providers.arm.paging import next_link
from mockazure.providers.arm.query import invalid, parse_filter
from mockazure.providers.arm.scopes import parse_scope
from mockazure.providers.graph.continuations import (
    CursorState,
    binding_for,
    resolve_cursor,
)
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.query.ast import Compare, Field, Literal, QuerySpec

AUTH = "Microsoft.Authorization/"


def rbac_parameters(request, operation, *, listing=False):
    args = request.query_params
    try:
        operation_contract(operation, args.get("api-version"))
    except ValueError:
        raise ArmError(400, "InvalidApiVersionParameter", "Unsupported RBAC API version") from None
    allowed = {"api-version"} | ({"$filter", "$skipToken"} if listing else set())
    if set(args) - allowed or len(args) != len(args.multi_items()):
        invalid("Unsupported or duplicate RBAC parameters")
    if "$skipToken" in args and set(args) != {"api-version", "$skipToken"}:
        invalid("Continuation parameters cannot change")
    return args["api-version"]


def role_view(name, role, scope):
    return {
        "id": f"/subscriptions/{scope.subscription_id}/providers/"
        f"Microsoft.Authorization/roleDefinitions/{role['id']}",
        "name": role["id"],
        "type": "Microsoft.Authorization/roleDefinitions",
        "properties": {
            "roleName": name,
            "description": role["description"],
            "type": "BuiltInRole",
            "assignableScopes": list(role["assignableScopes"]),
            "permissions": [
                {
                    key: list(role[key])
                    for key in ("actions", "notActions", "dataActions", "notDataActions")
                }
            ],
        },
    }


def definitions(scope, identifier=None, filter_text=None):
    values = [role_view(name, role, scope) for name, role in builtin_roles().items()]
    if identifier is not None:
        try:
            identifier = str(UUID(identifier))
        except ValueError:
            invalid("Invalid role definition ID")
        found = next((v for v in values if v["name"] == identifier), None)
        if found is None:
            raise ArmError(404, "RoleDefinitionDoesNotExist", "Role definition was not found")
        return found
    if filter_text and filter_text != "atScopeAndBelow()":
        node = parse_filter(filter_text)
        if (
            not isinstance(node, Compare)
            or node.op != "eq"
            or node.left.name not in {"roleName", "type"}
            or not isinstance(node.right.value, str)
        ):
            invalid("Unsupported role definition filter")
        values = [v for v in values if v["properties"][node.left.name] == node.right.value]
    return {"value": values}


async def row_view(session, row):
    if row.client_id is not None:
        identifier = await session.scalar(
            select(ServicePrincipal.id).where(
                ServicePrincipal.tenant_id == row.tenant_id,
                ServicePrincipal.client_id == row.client_id,
            )
        )
        kind = "ServicePrincipal"
    else:
        identifier, kind = (
            (row.user_id, "User") if row.user_id is not None else (row.group_id, "Group")
        )
    if identifier is None:
        raise ValueError("Assignment principal identity is missing")
    return assignment_view(row, identifier, kind)


async def principal_type(session, tenant, identifier, supplied=None):
    for model, kind in ((User, "User"), (ServicePrincipal, "ServicePrincipal"), (Group, "Group")):
        row = await session.get(model, identifier)
        if row is not None and row.tenant_id == tenant and row.deleted_at is None:
            if supplied is not None and supplied != kind:
                raise ArmError(
                    400, "InvalidPrincipalType", "Principal type does not match local identity"
                )
            return kind
    raise ArmError(404, "PrincipalNotFound", "Principal was not found in this tenant")


async def assignment_get(session, actor, scope, identifier):
    await require_action(session, actor, scope, AUTH + "roleAssignments/read")
    row = await session.get(RoleAssignment, identifier)
    if row is None or row.tenant_id != actor.tenant_id or row.scope_key != scope.key:
        raise ArmError(404, "RoleAssignmentNotFound", "Assignment was not found at this scope")
    return await row_view(session, row)


async def assignment_mutate(request, scope, identifier, value):
    action = AUTH + "roleAssignments/" + ("delete" if request.method == "DELETE" else "write")
    async with transaction(request, scope) as (session, actor, sub):
        await require_action(session, actor, scope, action)
        existing = await session.get(RoleAssignment, identifier)
        if existing and (existing.tenant_id != actor.tenant_id or existing.scope_key != scope.key):
            raise ArmError(404, "RoleAssignmentNotFound", "Assignment was not found at this scope")
        before = await row_view(session, existing) if existing else {}
        if request.method == "DELETE":
            if not existing:
                return None, 204
            await session.delete(existing)
            sub.revision += 1
            after, result, status = {}, before, 200
        else:
            validate_arm_payload("RoleAssignmentCreateParameters", value)
            if set(value) != {"properties"}:
                raise ValueError("Assignment requires properties only")
            props = value["properties"]
            if set(props) - {"principalId", "principalType", "roleDefinitionId", "description"}:
                raise ValueError("Unsupported assignment condition or property")
            description = props.get("description")
            if description is not None and len(description) > 1024:
                raise ValueError("Assignment description exceeds local limit")
            match = re.fullmatch(
                r"(?i)/subscriptions/([0-9a-f-]{36})/providers/Microsoft\.Authorization/"
                r"roleDefinitions/([0-9a-f-]{36})",
                props["roleDefinitionId"],
            )
            if not match or UUID(match[1]) != scope.subscription_id:
                raise ValueError("A local subscription-qualified role definition is required")
            assignee = UUID(props["principalId"])
            kind = await principal_type(
                session, actor.tenant_id, assignee, props.get("principalType")
            )
            row = await set_assignment(
                session,
                actor.tenant_id,
                scope.subscription_id,
                identifier,
                scope,
                str(UUID(match[2])),
                assignee,
                kind,
                description,
            )
            after = result = await row_view(session, row)
            status = 200 if existing else 201
        await append_arm_fact(
            session, actor, action, scope, before, after, datetime.now(UTC), uuid4()
        )
        return result, status


async def assignments_list(request, session, actor, scope):
    await require_action(session, actor, scope, AUTH + "roleAssignments/read")
    sub = await resolve_scope(
        session, actor.tenant_id, parse_scope(f"/subscriptions/{scope.subscription_id}")
    )
    binding = await binding_for(session, actor, "2022-04-01", "arm-rbac:" + scope.key)
    token = request.query_params.get("$skipToken")
    if token:
        try:
            state = await resolve_cursor(session, token, binding, datetime.now(UTC))
        except GraphError as error:
            raise ArmError(
                error.status, "InvalidSkipToken", "RBAC continuation is unavailable"
            ) from None
        if state.upper_revision != sub.revision:
            invalid("Assignments changed; restart collection")
        filter_text = state.query.filter.right.value
    else:
        filter_text = request.query_params.get("$filter", "")
        state = CursorState(
            binding,
            QuerySpec(filter=Compare("eq", Field("filter"), Literal(filter_text))),
            None,
            sub.revision,
            None,
            "page",
            datetime.now(UTC) + timedelta(hours=1),
        )
    query = select(RoleAssignment).where(
        RoleAssignment.tenant_id == actor.tenant_id,
        RoleAssignment.subscription_id == scope.subscription_id,
    )
    ancestors = RoleAssignment.scope_key.in_((*scope.parent_keys, scope.key))
    descendants = func.left(RoleAssignment.scope_key, len(scope.key) + 1) == scope.key + "/"
    query = query.where(ancestors if filter_text == "atScope()" else or_(ancestors, descendants))
    if filter_text not in {"", "atScope()"}:
        match = re.fullmatch(
            r"principalId eq (?P<quote>'?)(?P<id>[0-9a-fA-F-]{36})(?P=quote)", filter_text
        )
        if not match:
            invalid("Only atScope() and principalId equality are supported")
        try:
            principal_id = UUID(match["id"])
        except ValueError:
            invalid("Invalid principal ID")
        clients = select(ServicePrincipal.client_id).where(
            ServicePrincipal.tenant_id == actor.tenant_id, ServicePrincipal.id == principal_id
        )
        query = query.where(
            or_(
                RoleAssignment.user_id == principal_id,
                RoleAssignment.group_id == principal_id,
                RoleAssignment.client_id.in_(clients),
            )
        )
    if state.last_key:
        query = query.where(RoleAssignment.id > UUID(state.last_key[0]))
    rows = list(await session.scalars(query.order_by(RoleAssignment.id).limit(101)))
    result = {"value": [await row_view(session, row) for row in rows[:100]]}
    if len(rows) > 100:
        result["nextLink"] = await next_link(
            request, session, state, rows[99].id, token_key="$skipToken"
        )
    return result

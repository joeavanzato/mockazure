"""ARM inventory is filtered by effective scope in SQL before pagination."""

from fastapi import APIRouter, Request
from sqlalchemy import select

from mockazure.providers.arm.authorization import grant_query, readable_scopes, require_action
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import inventory_view, resolve_scope
from mockazure.providers.arm.models import ArmResource, RoleAssignment
from mockazure.providers.arm.paging import next_link, parameters, read_page
from mockazure.providers.arm.probes import principal
from mockazure.providers.arm.query import compile_inventory_filter, uses_tags
from mockazure.providers.arm.scopes import parse_scope

router = APIRouter()


def request_scope(subscription, group=None):
    try:
        return parse_scope(
            "/subscriptions/"
            + subscription
            + ("/resourceGroups/" + group if group is not None else "")
        )
    except ValueError:
        raise ArmError(400, "InvalidResourceId", "Invalid ARM scope") from None


async def inventory_list(request, subscription, group=None, *, groups=False):
    operation = "groups.list" if groups else "resources.group.list" if group else "resources.list"
    version = parameters(request, operation, collection=True, inventory=True)
    if groups and "$expand" in request.query_params:
        raise ArmError(400, "InvalidQueryParameterValue", "Resource groups do not support expand")
    scope = request_scope(subscription, group)
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        row = await resolve_scope(session, actor.tenant_id, scope)
        action = (
            "Microsoft.Resources/subscriptions/resourceGroups/read"
            if groups
            else ("Microsoft.Resources/subscriptions/resources/read")
        )
        grants = await grant_query(session, actor, action)
        if not await session.scalar(
            grants.where(RoleAssignment.subscription_id == scope.subscription_id).limit(1)
        ):
            raise ArmError(403, "AuthorizationFailed", "No accessible ARM scopes")
        if group:
            await require_action(session, actor, scope, action)
            sub = await resolve_scope(session, actor.tenant_id, request_scope(subscription))
        else:
            sub = row
        state = await read_page(request, session, actor, version, sub.revision, inventory=True)
        predicate = compile_inventory_filter(state.query.filter, groups=groups)
        visible = await readable_scopes(session, actor, action)
        query = (
            select(ArmResource)
            .where(
                ArmResource.tenant_id == actor.tenant_id,
                ArmResource.subscription_id == scope.subscription_id,
                ArmResource.kind == ("group" if groups else "resource"),
                visible,
                predicate,
            )
            .order_by(ArmResource.scope_key)
            .limit(state.query.top + 1)
        )
        if group:
            query = query.where(ArmResource.group_key == scope.key)
        if state.last_key:
            query = query.where(ArmResource.scope_key > state.last_key[0])
        rows = list(await session.scalars(query))
        result = [
            inventory_view(row, tuple(e.field for e in state.query.expand))
            for row in rows[: state.query.top]
        ]
        if uses_tags(state.query.filter):
            for value in result:
                value.pop("tags", None)
        body = {"value": result}
        if len(rows) > state.query.top:
            body["nextLink"] = await next_link(
                request, session, state, rows[state.query.top - 1].scope_key
            )
        return body


@router.get("/subscriptions/{subscription}/resourcegroups")
async def groups_list(subscription: str, request: Request):
    return await inventory_list(request, subscription, groups=True)


@router.get("/subscriptions/{subscription}/resources")
async def resources_list(subscription: str, request: Request):
    return await inventory_list(request, subscription)


@router.get("/subscriptions/{subscription}/resourcegroups/{group}/resources")
async def group_resources(subscription: str, group: str, request: Request):
    return await inventory_list(request, subscription, group)


@router.get("/subscriptions/{subscription}/resourcegroups/{group}")
async def group_get(subscription: str, group: str, request: Request):
    parameters(request, "groups.get")
    scope = request_scope(subscription, group)
    async with request.app.state.database.sessions() as session:
        actor = await principal(request, session)
        await require_action(
            session, actor, scope, "Microsoft.Resources/subscriptions/resourceGroups/read"
        )
        return inventory_view(await resolve_scope(session, actor.tenant_id, scope))

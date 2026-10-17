"""ARM tenant and subscription discovery backed by current identities and grants."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from sqlalchemy import select

from mockazure.db.models import Tenant
from mockazure.identity.tokens import validate_access_token
from mockazure.oauth.errors import OAuthError
from mockazure.providers.arm.authorization import current_principal, grant_query, require_action
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import resolve_scope
from mockazure.providers.arm.models import RoleAssignment, Subscription
from mockazure.providers.arm.paging import next_link, parameters, read_page
from mockazure.providers.arm.scopes import parse_scope

router = APIRouter()


async def principal(request, session):
    scheme, _, raw = request.headers.get("authorization", "").partition(" ")
    try:
        if scheme.lower() != "bearer":
            raise OAuthError("invalid_token", "ARM token required", 401)
        context = await validate_access_token(
            raw, "arm", datetime.now(UTC), session, endpoints=request.app.state.settings.endpoints
        )
    except OAuthError:
        raise ArmError(401, "InvalidAuthenticationToken", "ARM access token required") from None
    await current_principal(session, context)
    return context


def projection(row):
    return {
        "id": f"/subscriptions/{row.id}",
        "subscriptionId": str(row.id),
        "tenantId": str(row.tenant_id),
        "displayName": row.display_name,
        "state": row.state,
        "authorizationSource": "RoleBased",
        "tags": dict(row.tags),
    }


@router.get("/tenants")
async def tenants(request: Request):
    parameters(request, "tenants.list")
    async with request.app.state.database.sessions() as session:
        actor = await principal(request, session)
        tenant = await session.get(Tenant, actor.tenant_id)
        return {
            "value": [
                {
                    "id": f"/tenants/{tenant.id}",
                    "tenantId": str(tenant.id),
                    "displayName": tenant.display_name,
                    "tenantCategory": "Home",
                }
            ],
            "nextLink": "",
        }


@router.get("/subscriptions")
async def subscriptions(request: Request):
    version = parameters(request, "subscriptions.list", collection=True)
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        granted = await grant_query(session, actor, "Microsoft.Resources/subscriptions/read")
        permitted = granted.with_only_columns(RoleAssignment.subscription_id)
        state = await read_page(request, session, actor, version, 0)
        query = (
            select(Subscription)
            .where(Subscription.tenant_id == actor.tenant_id, Subscription.id.in_(permitted))
            .order_by(Subscription.id)
            .limit(101)
        )
        if state.last_key:
            from uuid import UUID

            query = query.where(Subscription.id > UUID(state.last_key[0]))
        rows = list(await session.scalars(query))
        return {
            "value": [projection(row) for row in rows[:100]],
            "nextLink": await next_link(request, session, state, rows[99].id)
            if len(rows) > 100
            else "",
        }


@router.get("/subscriptions/{subscription}")
async def subscription_get(subscription: str, request: Request):
    parameters(request, "subscriptions.get")
    try:
        scope = parse_scope("/subscriptions/" + subscription)
    except ValueError:
        raise ArmError(400, "InvalidResourceId", "Invalid subscription ID") from None
    async with request.app.state.database.sessions() as session:
        actor = await principal(request, session)
        await require_action(session, actor, scope, "Microsoft.Resources/subscriptions/read")
        return projection(await resolve_scope(session, actor.tenant_id, scope))

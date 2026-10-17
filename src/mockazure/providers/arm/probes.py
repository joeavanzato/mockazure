from datetime import UTC, datetime
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select

from mockazure.identity.tokens import validate_access_token
from mockazure.oauth.errors import OAuthError
from mockazure.providers.arm.authorization import current_principal
from mockazure.providers.arm.models import ReaderAssignment, Subscription
from mockazure.providers.graph.errors import GraphError

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
        raise GraphError(401, "InvalidAuthenticationToken", "ARM access token required") from None
    await current_principal(session, context)
    return context


def parameters(request):
    if (
        request.query_params.get("api-version") != "2021-01-01"
        or set(request.query_params) - {"api-version", "$skiptoken"}
        or len(request.query_params.multi_items()) != len(request.query_params)
    ):
        raise GraphError(400, "InvalidApiVersionParameter", "Supported api-version is 2021-01-01")
    try:
        return (
            UUID(request.query_params["$skiptoken"])
            if "$skiptoken" in request.query_params
            else None
        )
    except ValueError:
        raise GraphError(400, "InvalidSkipToken", "Invalid continuation cursor") from None


def projection(row):
    return {
        "id": f"/subscriptions/{row.id}",
        "subscriptionId": str(row.id),
        "tenantId": str(row.tenant_id),
        "displayName": row.display_name,
        "state": row.state,
        "authorizationSource": "RoleBased",
    }


@router.get("/subscriptions")
async def subscriptions(request: Request):
    after = parameters(request)
    async with request.app.state.database.sessions() as session:
        context = await principal(request, session)
        permitted = select(ReaderAssignment.subscription_id).where(
            ReaderAssignment.tenant_id == context.tenant_id,
            (
                ReaderAssignment.client_id == context.client_id
                if context.token_kind == "application"
                else ReaderAssignment.user_id == context.subject_id
            ),
        )
        query = (
            select(Subscription)
            .where(Subscription.tenant_id == context.tenant_id, Subscription.id.in_(permitted))
            .order_by(Subscription.id)
            .limit(101)
        )
        if after:
            query = query.where(Subscription.id > after)
        rows = list(await session.scalars(query))
        result = {"value": [projection(row) for row in rows[:100]]}
        if len(rows) > 100:
            profile = request.app.state.settings.advertised_endpoints(
                request.headers.get("host", "")
            )
            result["nextLink"] = (
                profile.arm
                + "/subscriptions?"
                + urlencode({"api-version": "2021-01-01", "$skiptoken": str(rows[99].id)})
            )
        return result

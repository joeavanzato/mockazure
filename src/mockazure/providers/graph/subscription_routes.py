"""Graph v1.0 subscription management; callbacks remain on registered local receivers."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select

from mockazure.authorization.evaluator import AuthorizationDenied, OperationRequirement, authorize
from mockazure.calendar.contracts import invalid
from mockazure.mail.send import lock_workflow
from mockazure.notifications.models import GraphSubscription
from mockazure.notifications.subscriptions import (
    mutate_subscription,
    owned,
    public,
    require_current,
)
from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import principal

router = APIRouter()


async def handle(request: Request):
    if request.query_params:
        invalid("Subscription management does not accept query options")
    try:
        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            await lock_workflow(session, actor.tenant_id)
            now = getattr(request.app.state, "clock", lambda: datetime.now(UTC))()
            identifier = request.path_params.get("subscription_id")
            if request.method == "GET":
                if identifier:
                    return JSONResponse(public(await owned(session, actor, identifier, now)))
                permissions = {
                    "Mail.Read",
                    "Mail.ReadWrite",
                    "Calendars.Read",
                    "Calendars.ReadWrite",
                    "Files.Read.All",
                    "Files.ReadWrite.All",
                }
                await authorize(
                    actor,
                    OperationRequirement(
                        "graph", frozenset(permissions), frozenset(permissions | {"Mail.ReadBasic"})
                    ),
                    session,
                )
                stmt = select(GraphSubscription).where(
                    GraphSubscription.tenant_id == actor.tenant_id,
                    GraphSubscription.client_id == actor.client_id,
                    GraphSubscription.deleted.is_(False),
                    GraphSubscription.expires_at > now,
                )
                if actor.token_kind == "delegated":
                    stmt = stmt.where(GraphSubscription.subject_id == actor.subject_id)
                result = []
                for row in await session.scalars(stmt.order_by(GraphSubscription.id).limit(1001)):
                    try:
                        await require_current(session, actor, row.target)
                    except (AuthorizationDenied, GraphError):
                        continue
                    result.append(public(row))
                if len(result) > 1000:
                    raise GraphError(
                        413, "ErrorResponseTooLarge", "Local subscription list limit exceeded"
                    )
                return JSONResponse({"value": result})
            operation = (
                "reauthorize"
                if request.url.path.endswith("/reauthorize")
                else {"POST": "create", "PATCH": "update", "DELETE": "delete"}[request.method]
            )
            body = await read_json(request, allow_empty=operation in {"delete", "reauthorize"})
            result = await mutate_subscription(session, actor, identifier, operation, body, now)
            if result is None:
                return Response(status_code=204)
            return JSONResponse(result, status_code=201 if operation == "create" else 200)
    except AuthorizationDenied:
        raise GraphError(
            403, "Authorization_RequestDenied", "Subscription operation is not permitted"
        ) from None


router.add_api_route("/v1.0/subscriptions", handle, methods=["GET", "POST"])
router.add_api_route(
    "/v1.0/subscriptions/{subscription_id}", handle, methods=["GET", "PATCH", "DELETE"]
)
router.add_api_route("/v1.0/subscriptions/{subscription_id}/reauthorize", handle, methods=["POST"])


@router.post(
    "/_mock/tenants/{tenant_id}/notifications/{subscription_id}/lifecycle", status_code=202
)
async def simulate_lifecycle(request: Request):
    from uuid import UUID

    from mockazure.admin.errors import AdminError
    from mockazure.admin.evidence_routes import tenant_session
    from mockazure.notifications.publication import emit_lifecycle

    try:
        tenant = UUID(request.path_params["tenant_id"])
        identifier = UUID(request.path_params["subscription_id"])
    except ValueError:
        raise AdminError(404, "NotFound", "Subscription was not found") from None
    body = await read_json(request, limit=1024)
    if set(body) != {"event"} or not isinstance(body["event"], str):
        invalid("Specify a lifecycle event")
    async with tenant_session(request, tenant) as session:
        await lock_workflow(session, tenant)
        sub = await session.get(GraphSubscription, identifier)
        if not sub or sub.tenant_id != tenant:
            raise AdminError(404, "NotFound", "Subscription was not found")
        await emit_lifecycle(session, sub, body["event"], datetime.now(UTC))
    return {"queued": True}

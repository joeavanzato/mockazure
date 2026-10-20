"""Graph v1.0 manager references and session revocation."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.directory.repository import public_object
from mockazure.identity.lifecycle import require_live_user, revoke_user_sessions, set_manager
from mockazure.providers.graph.body import invalid, read_json
from mockazure.providers.graph.directory import get_object
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.permissions import USER_READ, readable_fields
from mockazure.providers.graph.probes import context_url, permitted, principal
from mockazure.providers.graph.serialization import selected_fields, serialize_object
from mockazure.providers.graph.writes import checked_reference

router = APIRouter(prefix="/v1.0")


async def target(request, session, actor, identifier):
    if identifier is None:
        if actor.token_kind != "delegated":
            raise GraphError(403, "Authorization_RequestDenied", "The me resource requires a user")
        return await require_live_user(session, actor.tenant_id, actor.subject_id)
    return await get_object(session, actor.tenant_id, "user", identifier)


@router.post("/users/{identifier}/revokeSignInSessions")
@router.post("/me/revokeSignInSessions")
async def revoke(request: Request, identifier: str | None = None):
    if request.query_params or await request.body():
        invalid("Session revocation accepts no query or request content")
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        user = await target(request, session, actor, identifier)
        value = await revoke_user_sessions(session, actor, user.id, datetime.now(UTC))
        return {"@odata.context": context_url(request, "Edm.Boolean"), **value}


@router.put("/users/{identifier}/manager/$ref")
@router.delete("/users/{identifier}/manager/$ref")
async def manager_write(identifier: str, request: Request):
    if request.query_params:
        invalid("Manager writes accept no query options")
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        user = await target(request, session, actor, identifier)
        manager_id = None
        if request.method == "PUT":
            body = await read_json(request)
            if set(body) != {"@odata.id"}:
                invalid("Manager assignment requires only @odata.id")
            settings = request.app.state.settings
            origins = frozenset(
                [
                    settings.endpoints.graph,
                    *(p.graph for p in settings.client_profiles.values()),
                ]
            )
            manager_id = await checked_reference(
                session, body["@odata.id"], origins, actor.tenant_id
            )
        elif await request.body():
            invalid("Manager removal accepts no request content")
        await set_manager(session, actor, user.id, manager_id, datetime.now(UTC))
        return Response(status_code=204)


@router.get("/users/{identifier}/manager")
@router.get("/me/manager")
async def manager_read(request: Request, identifier: str | None = None):
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        user = await target(request, session, actor, identifier)
        await permitted(actor, OperationRequirement("graph", frozenset(), USER_READ), session)
        if user.manager_id is None:
            raise GraphError(404, "Request_ResourceNotFound", "Manager not found")
        manager = await require_live_user(session, actor.tenant_id, user.manager_id)
        value = serialize_object(
            public_object(manager),
            selected_fields(request),
            readable_fields(actor, "user", manager.id),
        )
        return {
            "@odata.context": context_url(request, "directoryObjects/$entity"),
            "@odata.type": "#microsoft.graph.user",
            **value,
        }

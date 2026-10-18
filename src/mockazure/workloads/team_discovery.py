"""Membership-filtered team and chat discovery with durable pages."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select

from mockazure.identity.models import User
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import context_url
from mockazure.workloads.cursors import binding, resolve_cursor
from mockazure.workloads.models import WorkloadScope as S
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.queries import invalid, snapshots
from mockazure.workloads.team_access import check_operation, member_exists
from mockazure.workloads.team_queries import collection, options


async def user_id(session, actor, identifier):
    if identifier == "me":
        if actor.token_kind != "delegated":
            raise GraphError(403, "Forbidden", "This operation requires a signed-in user")
        return actor.subject_id
    try:
        condition = User.id == UUID(identifier)
    except ValueError:
        condition = func.lower(User.upn) == identifier.lower()
    user = await session.scalar(
        select(User.id).where(
            User.tenant_id == actor.tenant_id, User.deleted_at.is_(None), condition
        )
    )
    if user is None:
        raise GraphError(404, "NotFound", "Local user was not found")
    if actor.token_kind == "delegated" and actor.subject_id != user:
        raise GraphError(403, "Forbidden", "Delegated discovery is limited to the signed-in user")
    return user


async def discover(request, session, actor, identifier, kind):
    user = await user_id(session, actor, identifier)
    operation = "joinedTeams" if kind == "team" else "chats"
    await check_operation(session, actor, operation)
    resource = request.scope["path"]
    token = request.query_params.get("$skiptoken")
    if token:
        if len(request.query_params.multi_items()) != 1:
            invalid("A continuation cannot change its query")
        state = await resolve_cursor(session, actor, resource, token, datetime.now(UTC))
        for name, saved in sorted(state["scopes"].items()):
            row = await session.scalar(
                select(S)
                .where(S.tenant_id == actor.tenant_id, S.workload == "teams", S.scope == name)
                .with_for_update(read=True)
            )
            if row is None or str(row.generation) != saved["generation"]:
                raise GraphError(410, "resyncRequired", "Conversation state was reset")
            if row.grant_revision != saved["grants"]:
                raise GraphError(403, "Forbidden", "Conversation membership changed")
    else:
        scopes = list(
            await session.scalars(
                select(S)
                .where(
                    S.tenant_id == actor.tenant_id,
                    S.workload == "teams",
                    member_exists(actor, kind, S.scope, S.scope, user),
                )
                .order_by(S.scope)
                .limit(10001)
                .with_for_update(read=True)
            )
        )
        if len(scopes) > 10000:
            raise GraphError(413, "responseTooLarge", "More than10000 conversation scopes")
        state = {
            "workload": "teams",
            "binding": binding(actor, resource),
            "offset": 0,
            "options": options(request, operation, kind),
            "scopes": {
                row.scope: {
                    "upper": row.revision,
                    "generation": str(row.generation),
                    "grants": row.grant_revision,
                }
                for row in scopes
            },
        }
    if not state["scopes"]:
        return {"@odata.context": context_url(request, kind), "value": []}
    query = select(V).where(
        V.tenant_id == actor.tenant_id,
        V.workload == "teams",
        V.kind == kind,
        V.deleted.is_(False),
        snapshots(state),
        member_exists(actor, kind, V.id, V.scope, user),
    )
    return await collection(
        request, session, actor, query, state, token, kind, operation, next(iter(state["scopes"]))
    )

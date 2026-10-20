"""Scope-locked native Teams revisions and separate current authority."""

import copy
from uuid import UUID

from sqlalchemy import delete, select, update

from mockazure.providers.graph.errors import GraphError
from mockazure.workloads.models import ConversationAccess as A
from mockazure.workloads.models import WorkloadRelation as E
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.teams import validate_team_record


async def record_at(session, tenant, scope, kind, identifier):
    row = await session.scalar(
        select(V).where(
            V.tenant_id == tenant,
            V.workload == "teams",
            V.scope == scope,
            V.kind == kind,
            V.payload["id"].as_string() == identifier,
            V.valid_to.is_(None),
            V.deleted.is_(False),
        )
    )
    if row is None:
        raise GraphError(404, "NotFound", "Teams resource was not found")
    return row


async def child_at(session, tenant, parent, relation, identifier):
    row = await session.scalar(
        select(V)
        .join(
            E,
            (
                (E.tenant_id == V.tenant_id)
                & (E.workload == V.workload)
                & (E.scope == V.scope)
                & (E.target_kind == V.kind)
                & (E.target_id == V.id)
            ),
        )
        .where(
            V.tenant_id == tenant,
            V.workload == "teams",
            V.scope == parent.scope,
            V.payload["id"].as_string() == identifier,
            V.valid_to.is_(None),
            V.deleted.is_(False),
            E.source_kind == parent.kind,
            E.source_id == parent.id,
            E.relation == relation,
            E.valid_to.is_(None),
        )
    )
    if row is None:
        raise GraphError(404, "NotFound", "Teams child resource was not found")
    return row


async def write_record(session, state, kind, payload, *, previous=None, deleted=False):
    if not deleted:
        validate_team_record(kind, payload)
    if previous:
        previous.valid_to = state.revision
    row = V(
        tenant_id=state.tenant_id,
        workload="teams",
        scope=state.scope,
        kind=kind,
        id=previous.id if previous else payload["id"],
        valid_from=state.revision,
        payload=copy.deepcopy(payload),
        deleted=deleted,
        name=payload.get("displayName") or payload.get("topic"),
    )
    session.add(row)
    await session.flush()
    return row


def add_edge(session, state, parent, relation, child):
    session.add(
        E(
            tenant_id=state.tenant_id,
            workload="teams",
            scope=state.scope,
            source_kind=parent.kind,
            source_id=parent.id,
            relation=relation,
            target_kind=child.kind,
            target_id=child.id,
            valid_from=state.revision,
        )
    )


async def set_membership(session, state, conversation, user_id, roles, *, remove=False):
    conditions = (
        A.tenant_id == state.tenant_id,
        A.scope == state.scope,
        A.resource_kind == conversation.kind,
        A.resource_id == conversation.payload["id"],
        A.principal_kind == "user",
        A.principal_id == user_id,
        A.permission.in_(["member", "owner"]),
    )
    await session.execute(delete(A).where(*conditions))
    if not remove:
        for permission in ("member", "owner") if "owner" in roles else ("member",):
            session.add(
                A(
                    tenant_id=state.tenant_id,
                    scope=state.scope,
                    resource_kind=conversation.kind,
                    resource_id=conversation.payload["id"],
                    principal_kind="user",
                    principal_id=user_id,
                    permission=permission,
                )
            )
    state.grant_revision += 1


async def remove_member(session, state, conversation, member):
    user_id = UUID(member.payload["userId"])
    if "owner" in member.payload.get("roles", []):
        other = await session.scalar(
            select(A.id)
            .where(
                A.tenant_id == state.tenant_id,
                A.scope == state.scope,
                A.resource_kind == conversation.kind,
                A.resource_id == conversation.payload["id"],
                A.permission == "owner",
                A.principal_id != user_id,
            )
            .limit(1)
        )
        if other is None:
            raise GraphError(400, "BadRequest", "A conversation must retain an authoritative owner")
    await write_record(session, state, member.kind, {}, previous=member, deleted=True)
    await session.execute(
        update(E)
        .where(
            E.tenant_id == state.tenant_id,
            E.workload == "teams",
            E.scope == state.scope,
            E.target_kind == member.kind,
            E.target_id == member.id,
            E.valid_to.is_(None),
        )
        .values(valid_to=state.revision)
    )
    await set_membership(session, state, conversation, user_id, [], remove=True)

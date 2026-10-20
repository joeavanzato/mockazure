"""Native file authority and revisions; observed permission rows never grant access."""

import copy
from uuid import uuid4

from sqlalchemy import func, select

from mockazure.authorization.evaluator import AuthorizationDenied, OperationRequirement, authorize
from mockazure.content.repository import pin_content
from mockazure.providers.graph.errors import GraphError
from mockazure.workloads.models import WorkloadGrant as G
from mockazure.workloads.models import WorkloadRelation as E
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.repository import pin_owner
from mockazure.workloads.sites import validate_site_drive_record

WRITE = frozenset({"Files.ReadWrite.All", "Sites.ReadWrite.All"})


def invalid(message):
    raise GraphError(400, "invalidRequest", message)


def name_input(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 255
        or value in {".", ".."}
        or value.endswith((".", " "))
        or any(ord(c) < 32 or c in '/\\:*?"<>|' for c in value)
    ):
        invalid("A bounded valid file name is required")
    return value


async def require_write(session, actor, scope):
    try:
        await authorize(
            actor,
            OperationRequirement(
                "graph",
                WRITE | {"Sites.Selected"},
                WRITE | {"Files.ReadWrite", "Sites.Selected"},
            ),
            session,
        )
    except AuthorizationDenied:
        raise GraphError(403, "accessDenied", "Current file write permission is required") from None
    principals = []
    if not actor.permissions & WRITE and "Sites.Selected" in actor.permissions:
        principals.append(("application", actor.client_id))
    if actor.token_kind == "delegated":
        principals.append(("user", actor.subject_id))
    for kind, identifier in principals:
        granted = await session.scalar(
            select(G.id)
            .where(
                G.tenant_id == actor.tenant_id,
                G.workload == "sharepoint",
                G.scope == scope,
                G.principal_kind == kind,
                G.principal_id == identifier,
                G.role.in_(["write", "owner", "fullcontrol"]),
            )
            .limit(1)
        )
        if granted is None:
            raise GraphError(403, "accessDenied", "Current site write access is required")


def etag(row):
    return row.payload.get("eTag") or f'"{row.id},{row.valid_from}"'


def match(row, value):
    if value and value not in {"*", etag(row)}:
        raise GraphError(412, "preconditionFailed", "The item version has changed")


async def record(
    session, state, kind, payload, *, previous=None, content=None, deleted=False, key=None
):
    if not deleted:
        validate_site_drive_record(kind, payload)
    if previous:
        previous.valid_to = state.revision
    row = V(
        tenant_id=state.tenant_id,
        workload="sharepoint",
        scope=state.scope,
        kind=kind,
        id=previous.id if previous else key or payload["id"],
        valid_from=state.revision,
        payload=copy.deepcopy(payload),
        content_id=content,
        deleted=deleted,
        name=payload.get("name"),
    )
    session.add(row)
    await session.flush()
    if content:
        await pin_content(
            session,
            state.tenant_id,
            content,
            "workload-version",
            pin_owner("sharepoint", state.scope, kind, row.id, state.revision),
        )
    return row


def edge(session, state, parent, relation, child):
    session.add(
        E(
            tenant_id=state.tenant_id,
            workload="sharepoint",
            scope=state.scope,
            source_kind=parent.kind,
            source_id=parent.id,
            relation=relation,
            target_kind=child.kind,
            target_id=child.id,
            valid_from=state.revision,
        )
    )


async def sibling(session, state, parent, name):
    return await session.scalar(
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
            V.tenant_id == state.tenant_id,
            V.workload == "sharepoint",
            V.scope == state.scope,
            V.kind == "driveItem",
            V.valid_to.is_(None),
            V.deleted.is_(False),
            E.source_kind == "driveItem",
            E.source_id == parent.id,
            E.relation == "children",
            E.valid_to.is_(None),
            func.lower(V.name) == name.lower(),
        )
    )


async def history(session, state, item):
    if item.content_id is None:
        return
    # Internal keys disambiguate identical public version numbers on distinct items.
    version = await record(
        session,
        state,
        "driveItemVersion",
        {
            "id": str(state.revision) + ".0",
            "size": item.payload["size"],
            "lastModifiedDateTime": item.payload.get("lastModifiedDateTime"),
        },
        content=item.content_id,
        key=str(uuid4()),
    )
    edge(session, state, item, "versions", version)

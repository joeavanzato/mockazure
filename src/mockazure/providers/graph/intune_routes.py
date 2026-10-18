"""Read-only Graph v1.0 Intune inventory and observed policy evidence."""

from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import exists, func, select

from mockazure.identity.models import User
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import principal
from mockazure.workloads.intune import authorize_intune
from mockazure.workloads.intune_contracts import EDGES, ROOTS, SCOPE
from mockazure.workloads.intune_queries import collection, page_state, project
from mockazure.workloads.models import WorkloadRelation as E
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.queries import invalid
from mockazure.workloads.repository import visible

router = APIRouter(prefix="/v1.0")


def rows(actor, kind, revision):
    return select(V).where(
        V.tenant_id == actor.tenant_id,
        V.workload == "intune",
        V.scope == SCOPE,
        V.kind == kind,
        V.deleted.is_(False),
        visible(V, revision),
    )


def relationship(actor, source_kind, source, relation, revision):
    return exists(
        select(1).where(
            E.tenant_id == actor.tenant_id,
            E.workload == "intune",
            E.scope == SCOPE,
            E.source_kind == source_kind,
            E.source_id == source,
            E.relation == relation,
            E.target_kind == V.kind,
            E.target_id == V.id,
            visible(E, revision),
        )
    )


async def read(request, path, user=None):
    parts = path.split("/")
    if any(part in {"", ".", ".."} or "\\" in part for part in parts):
        invalid("Invalid Intune path")
    root_kind = ROOTS.get(parts[0])
    if root_kind is None or len(parts) > 4:
        raise GraphError(501, "NotSupported", "This Intune operation is unsupported")
    kind, parent = root_kind, None
    if len(parts) >= 3:
        matches = [
            target
            for source, relation, target in EDGES
            if source == root_kind and relation == parts[2]
        ]
        if len(matches) != 1 or user is not None:
            raise GraphError(501, "NotSupported", "This Intune navigation is unsupported")
        kind, parent = matches[0], parts[1]
    item = len(parts) in {2, 4}
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        await authorize_intune(session, actor, kind)
        state, token = await page_state(request, session, actor, kind, item)
        revision = state["revision"]
        query = rows(actor, kind, revision)
        if user is not None:
            if user == "me":
                if actor.token_kind != "delegated":
                    raise GraphError(403, "Forbidden", "A signed-in user is required")
                identity = actor.subject_id
            else:
                try:
                    condition = User.id == UUID(user)
                except ValueError:
                    condition = func.lower(User.upn) == user.lower()
                identity = await session.scalar(
                    select(User.id).where(
                        User.tenant_id == actor.tenant_id, User.deleted_at.is_(None), condition
                    )
                )
                if identity is None:
                    raise GraphError(404, "ResourceNotFound", "User was not found")
            query = query.where(
                relationship(actor, "user", str(identity), "managedDevices", revision)
            )
        if parent is not None:
            source = await session.scalar(
                rows(actor, root_kind, revision).where(V.payload["id"].as_string() == parent)
            )
            if source is None:
                raise GraphError(404, "ResourceNotFound", "Intune parent was not found")
            query = query.where(relationship(actor, root_kind, source.id, parts[2], revision))
        if item:
            row = await session.scalar(query.where(V.payload["id"].as_string() == parts[-1]))
            if row is None:
                raise GraphError(404, "ResourceNotFound", "Intune evidence was not found")
            return project(row, state["options"]["select"])
        return await collection(request, session, actor, query, state, token, kind)


@router.get("/deviceManagement/{path:path}")
async def intune(request: Request, path: str):
    return await read(request, path)


@router.get("/users/{user}/managedDevices")
@router.get("/me/managedDevices")
async def user_devices(request: Request, user: str = "me"):
    return await read(request, "managedDevices", user)


@router.get("/users/{user}/managedDevices/{device}")
@router.get("/me/managedDevices/{device}")
async def user_device(request: Request, device: str, user: str = "me"):
    return await read(request, "managedDevices/" + device, user)

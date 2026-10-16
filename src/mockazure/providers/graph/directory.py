"""Graph directory HTTP contracts; domain writes are added separately."""

from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select

from mockazure.directory.models import DirectoryEntry, DirectoryRelation, Group
from mockazure.directory.repository import public_object
from mockazure.identity.models import Application, ServicePrincipal, User
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.permissions import (
    GraphOperation,
    authorize_directory,
    readable_fields,
)
from mockazure.providers.graph.probes import context_url, principal
from mockazure.providers.graph.serialization import (
    DEFAULT_FIELDS,
    selected_fields,
    serialize_object,
)


def not_found():
    raise GraphError(404, "Request_ResourceNotFound", "Resource not found")


async def get_object(session, tenant_id, kind, value):
    model = User if kind == "user" else Group
    try:
        match = model.id == UUID(value)
    except ValueError:
        if kind != "user":
            not_found()
        match = User.upn == value.lower()
    row = await session.scalar(
        select(model).where(model.tenant_id == tenant_id, match, model.deleted_at.is_(None))
    )
    if row is None:
        not_found()
    return row


def router_for(version: str) -> APIRouter:
    if version not in {"v1.0", "beta"}:
        raise ValueError("Unsupported Graph version")
    router = APIRouter(prefix=f"/{version}")

    @router.get("/users")
    @router.get("/groups")
    async def collection(request: Request):
        kind = "user" if request.url.path.endswith("/users") else "group"
        resource = kind + "s"
        async with request.app.state.database.sessions() as session:
            context = await principal(request, session)
            await authorize_directory(
                session, context, GraphOperation(version, resource, "GET"), None
            )
            fields = selected_fields(request)
            model = User if kind == "user" else Group
            rows = (
                await session.scalars(
                    select(model)
                    .where(model.tenant_id == context.tenant_id, model.deleted_at.is_(None))
                    .order_by(model.id)
                    .limit(101)
                )
            ).all()
            if len(rows) > 100:
                raise GraphError(
                    400, "Request_UnsupportedQuery", "Paged collection support is pending"
                )
            value = [
                serialize_object(public_object(row), fields, readable_fields(context, kind, row.id))
                for row in rows
            ]
            return {"@odata.context": context_url(request, resource), "value": value}

    @router.get("/users/{object_id}")
    @router.get("/groups/{object_id}")
    async def entity(request: Request, object_id: str):
        kind = "user" if request.url.path.split("/")[2] == "users" else "group"
        async with request.app.state.database.sessions() as session:
            context = await principal(request, session)
            # Resolve authorization before lookup; self-UPN uses the signed-in user's own row only.
            target = None
            try:
                target = UUID(object_id)
            except ValueError:
                if context.subject_id is not None:
                    own = await session.get(User, context.subject_id)
                    if (
                        own is not None
                        and own.tenant_id == context.tenant_id
                        and own.upn == object_id.lower()
                    ):
                        target = own.id
            await authorize_directory(
                session, context, GraphOperation(version, kind + "s", "GET"), target
            )
            row = await get_object(session, context.tenant_id, kind, object_id)
            data = serialize_object(
                public_object(row), selected_fields(request), readable_fields(context, kind, row.id)
            )
            return {"@odata.context": context_url(request, kind + "s/$entity"), **data}

    @router.get("/groups/{object_id}/members")
    @router.get("/groups/{object_id}/owners")
    async def relationship(request: Request, object_id: str):
        resource = request.url.path.rsplit("/", 1)[-1]
        relation = "member" if resource == "members" else "owner"
        async with request.app.state.database.sessions() as session:
            context = await principal(request, session)
            await authorize_directory(
                session, context, GraphOperation(version, resource, "GET"), None
            )
            group = await get_object(session, context.tenant_id, "group", object_id)
            if (
                group.profile.get("visibility") == "HiddenMembership"
                and "Member.Read.Hidden" not in context.permissions
            ):
                raise GraphError(
                    403, "Authorization_RequestDenied", "Hidden membership permission is required"
                )
            fields = selected_fields(request)
            entries = (
                await session.scalars(
                    select(DirectoryEntry)
                    .join(
                        DirectoryRelation,
                        (DirectoryRelation.tenant_id == DirectoryEntry.tenant_id)
                        & (DirectoryRelation.object_id == DirectoryEntry.id),
                    )
                    .where(
                        DirectoryRelation.tenant_id == context.tenant_id,
                        DirectoryRelation.group_id == group.id,
                        DirectoryRelation.relation == relation,
                    )
                    .order_by(DirectoryEntry.id)
                    .limit(101)
                )
            ).all()
            if len(entries) > 100:
                raise GraphError(
                    400, "Request_UnsupportedQuery", "Paged relationship support is pending"
                )
            result = []
            for entry in entries:
                if entry.kind == "servicePrincipal":
                    if version == "v1.0":
                        continue
                    sp = await session.get(ServicePrincipal, entry.id)
                    app = await session.get(Application, sp.client_id)
                    data = {
                        "id": str(sp.id),
                        "displayName": app.display_name,
                        "appId": str(sp.client_id),
                        "accountEnabled": sp.enabled,
                    }
                    visible = readable_fields(context, entry.kind, entry.id)
                    selected = fields or tuple(data)
                    if not set(selected) <= data.keys():
                        raise GraphError(
                            400, "Request_UnsupportedQuery", "Unsupported member property"
                        )
                    item = {k: data[k] if k in visible else None for k in selected}
                else:
                    row = await get_object(session, context.tenant_id, entry.kind, str(entry.id))
                    visible = readable_fields(context, entry.kind, entry.id)
                    # A limited object retains the requested/default keys as explicit nulls.
                    item = serialize_object(
                        public_object(row), fields or DEFAULT_FIELDS[entry.kind], visible
                    )
                item["id"] = str(entry.id)
                item["@odata.type"] = f"#microsoft.graph.{entry.kind}"
                result.append(item)
            return {
                "@odata.context": context_url(request, f"groups/{group.id}/{resource}"),
                "value": result,
            }

    return router

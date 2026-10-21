"""Thin Graph write adapter; all state changes use the common directory service."""

from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mockazure.directory.models import DirectoryEntry
from mockazure.directory.mutations import DirectoryCommand, mutate_directory
from mockazure.directory.references import parse_local_reference
from mockazure.identity.models import ServicePrincipal
from mockazure.providers.graph.body import invalid, read_json
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import context_url, principal
from mockazure.providers.graph.serialization import serialize_object


async def checked_reference(session, value, origins, tenant):
    identifier = parse_local_reference(value, origins, tenant)
    collection = urlsplit(value).path.split("/")[2]
    if collection != "directoryObjects":
        kind = await session.scalar(
            select(DirectoryEntry.kind).where(
                DirectoryEntry.tenant_id == tenant, DirectoryEntry.id == identifier
            )
        )
        if kind is None and await session.scalar(
            select(ServicePrincipal.id).where(
                ServicePrincipal.tenant_id == tenant, ServicePrincipal.id == identifier
            )
        ):
            kind = "servicePrincipal"
        if kind is not None and collection != kind + "s":
            invalid("Reference collection does not match its object type")
    return identifier


def register_writes(router, version):
    @router.post("/users")
    @router.post("/groups")
    @router.patch("/users/{object_id}")
    @router.patch("/groups/{object_id}")
    @router.delete("/users/{object_id}")
    @router.delete("/groups/{object_id}")
    @router.post("/groups/{object_id}/members/$ref")
    @router.delete("/groups/{object_id}/members/{member_id}/$ref")
    async def write(request: Request, object_id: str | None = None, member_id: str | None = None):
        from mockazure.providers.graph.directory import get_object

        kind = "user" if request.url.path.split("/")[2] == "users" else "group"
        membership = request.url.path.endswith("/$ref")
        owner = membership and "/owners/" in request.url.path
        action = (
            {"POST": "add_member", "DELETE": "remove_member"}
            if membership
            else {"POST": "create", "PATCH": "update", "DELETE": "delete"}
        )[request.method]
        if owner:
            action = action.replace("_member", "_owner")
        if request.query_params:
            invalid("Query options are unsupported on directory writes")
        try:
            async with request.app.state.database.sessions.begin() as session:
                context = await principal(request, session)
                values = {} if request.method == "DELETE" else await read_json(request)
                if not membership and "@odata.type" in values:
                    if values["@odata.type"] != "#microsoft.graph." + kind:
                        invalid("Type annotation does not match the directory resource")
                    values = {key: value for key, value in values.items() if key != "@odata.type"}
                settings = request.app.state.settings
                origins = frozenset(
                    [
                        settings.endpoints.graph,
                        *(profile.graph for profile in settings.client_profiles.values()),
                    ]
                )
                if membership:
                    if request.method == "POST":
                        if set(values) != {"@odata.id"}:
                            invalid("Membership requires only @odata.id")
                        member = await checked_reference(
                            session, values["@odata.id"], origins, context.tenant_id
                        )
                    else:
                        try:
                            member = UUID(member_id)
                        except ValueError:
                            invalid("Invalid member identifier")
                    values = {"owner_id" if owner else "member_id": member}
                elif kind == "group" and action == "create":
                    values = dict(values)
                    for key in ("owners@odata.bind", "members@odata.bind"):
                        if key in values:
                            if not isinstance(values[key], list) or len(values[key]) > 20:
                                invalid("At most 20 combined initial references are supported")
                            values[key] = [
                                await checked_reference(session, x, origins, context.tenant_id)
                                for x in values[key]
                            ]
                target = None
                if object_id:
                    try:
                        target = UUID(object_id)
                    except ValueError:
                        target = (await get_object(session, context.tenant_id, kind, object_id)).id
                result = await mutate_directory(
                    session,
                    context.tenant_id,
                    DirectoryCommand(kind, action, target, values),
                    context,
                    datetime.now(UTC),
                    correlation_id=UUID(request.state.request_id),
                    expected_public_version=request.headers.get("x-mockazure-if-version"),
                )
                if action != "create":
                    return Response(status_code=204)
                data = serialize_object(result.object, None, frozenset(result.object.properties))
                from starlette.responses import JSONResponse

                return JSONResponse(
                    status_code=201,
                    content={"@odata.context": context_url(request, kind + "s/$entity"), **data},
                )
        except IntegrityError as error:
            code = getattr(error.orig, "sqlstate", None)
            if code in {"23505", "23503", "23514"}:
                raise GraphError(
                    400, "Request_BadRequest", "Conflicting or invalid directory state"
                ) from None
            raise

    if version == "v1.0":
        router.add_api_route("/groups/{object_id}/owners/$ref", write, methods=["POST"])
        router.add_api_route(
            "/groups/{object_id}/owners/{member_id}/$ref", write, methods=["DELETE"]
        )

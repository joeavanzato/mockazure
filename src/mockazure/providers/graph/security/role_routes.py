"""Modern role assignments and legacy role/member inspection share effective state."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.responses import JSONResponse

from mockazure.providers.graph.body import read_json, typed_body
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import principal
from mockazure.providers.graph.security.queries import parse_security_query
from mockazure.providers.graph.security.serialization import serialize_security
from mockazure.security.mutations import uuid_value
from mockazure.security.paging import security_collection
from mockazure.security.repository import document, lock_security, missing
from mockazure.security.roles import (
    definition_name,
    legacy_id,
    mutate_role_assignment,
    require_role_access,
    role_assignments_source,
    role_source,
    sync_activation,
)


def router_for(version):
    router = APIRouter(prefix="/" + version)

    def read_route(path, kind, members=False):
        async def read(request: Request):
            async with request.app.state.database.sessions.begin() as session:
                actor = await principal(request, session)
                await require_role_access(session, actor, legacy=kind == "directoryRole")
                await lock_security(session, actor.tenant_id)
                await sync_activation(session, actor.tenant_id)
                identifier = request.path_params.get("identifier")
                if "template" in request.path_params:
                    name = definition_name(request.path_params["template"])
                    identifier = str(legacy_id(actor.tenant_id, name))
                source = (
                    role_assignments_source(actor.tenant_id, actor)
                    if kind == "unifiedRoleAssignment"
                    else role_source(actor.tenant_id, kind)
                )
                if members:
                    role = await session.scalar(
                        select(source.c.payload).where(source.c.id == uuid_value(identifier))
                    )
                    if role is None:
                        missing()
                    assignments = role_assignments_source(actor.tenant_id, actor)
                    source = (
                        select(
                            assignments.c.principal_id.label("id"),
                            assignments.c.principal_payload.label("payload"),
                        )
                        .where(assignments.c.role_name == role["displayName"])
                        .subquery()
                    )
                query_kind = "directoryRoleMember" if members else kind
                if identifier and not members:
                    if set(request.query_params) - {"$select", "$expand"}:
                        raise GraphError(
                            400, "Request_UnsupportedQuery", "Item query is unsupported"
                        )
                    row = (
                        (
                            await session.execute(
                                select(source).where(source.c.id == uuid_value(identifier))
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if row is None:
                        missing()
                    query = parse_security_query(request, query_kind)
                    payload = dict(row["payload"])
                    for expansion in query.expand:
                        payload[expansion.field] = row[
                            "principal_payload"
                            if expansion.field == "principal"
                            else "role_payload"
                        ]
                    return serialize_security(payload, query)
                query = (
                    None
                    if "$skiptoken" in request.query_params
                    else parse_security_query(request, query_kind)
                )
                if query and query.expand:
                    payload = source.c.payload
                    for expansion in query.expand:
                        field = (
                            source.c.principal_payload
                            if expansion.field == "principal"
                            else source.c.role_payload
                        )
                        payload = payload.op("||")(document(**{expansion.field: field}))
                    source = select(source.c.id, payload.label("payload")).subquery()
                return await security_collection(
                    request, session, actor, query_kind, query, source=source
                )

        router.add_api_route(path, read, methods=["GET"])

    async def write(request: Request):
        if request.query_params:
            raise GraphError(400, "Request_UnsupportedQuery", "Write query is unsupported")
        identifier = request.path_params.get("identifier")
        body = {} if identifier else typed_body(await read_json(request), "unifiedRoleAssignment")
        try:
            async with request.app.state.database.sessions.begin() as session:
                actor = await principal(request, session)
                result = await mutate_role_assignment(
                    session, actor, identifier, body, datetime.now(UTC)
                )
        except IntegrityError:
            raise GraphError(
                409, "Request_Conflict", "Directory role assignment conflicts with state"
            ) from None
        return (
            JSONResponse(result, status_code=201)
            if result is not None
            else Response(status_code=204)
        )

    for resource, kind in (
        ("roleDefinitions", "unifiedRoleDefinition"),
        ("roleAssignments", "unifiedRoleAssignment"),
    ):
        path = "/roleManagement/directory/" + resource
        read_route(path, kind)
        read_route(path + "/{identifier}", kind)
    router.add_api_route("/roleManagement/directory/roleAssignments", write, methods=["POST"])
    router.add_api_route(
        "/roleManagement/directory/roleAssignments/{identifier}", write, methods=["DELETE"]
    )
    read_route("/directoryRoles", "directoryRole")
    for path in (
        "/directoryRoles/roleTemplateId={template}",
        "/directoryRoles(roleTemplateId='{template}')",
        "/directoryRoles/{identifier}",
    ):
        read_route(path, "directoryRole")
        read_route(path + "/members", "directoryRole", members=True)
    return router

"""Permission-checked Graph grant endpoints with stable PostgreSQL paging."""

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
from mockazure.security.grants import grant_item, grant_source, mutate_grant, read_grants
from mockazure.security.paging import security_collection
from mockazure.security.repository import (
    ensure_resource_principals,
    lock_security,
    missing,
    public_security_object,
)


def router_for(version):
    router = APIRouter(prefix="/" + version)

    def register(path, kind, methods, relation=None, alternate=False):
        async def handle(request: Request):
            identifier = request.path_params.get("identifier")
            subject = request.path_params.get("principal_id")
            read = request.method == "GET"
            body = None
            if not read:
                if request.query_params:
                    raise GraphError(400, "Request_UnsupportedQuery", "Write query is unsupported")
                body = (
                    {} if request.method == "DELETE" else typed_body(await read_json(request), kind)
                )
            try:
                async with request.app.state.database.sessions.begin() as session:
                    actor = await principal(request, session)
                    if read:
                        await read_grants(session, actor, kind)
                    await lock_security(session, actor.tenant_id)
                    await ensure_resource_principals(session, actor.tenant_id)
                    scope_field, scope_id = None, None
                    if subject:
                        sp = await public_security_object(
                            session,
                            actor.tenant_id,
                            "servicePrincipal",
                            subject,
                            by_appid=alternate,
                        )
                        scope_id = sp["id"]
                        scope_field = (
                            "clientId"
                            if kind == "oAuth2PermissionGrant"
                            else "resourceId"
                            if relation == "appRoleAssignedTo"
                            else "principalId"
                        )
                    current = None
                    if identifier:
                        current = (await grant_item(session, actor.tenant_id, kind, identifier))[
                            "payload"
                        ]
                        if scope_field and current[scope_field] != scope_id:
                            missing()
                    if read:
                        if identifier:
                            if set(request.query_params) - {"$select"}:
                                raise GraphError(
                                    400, "Request_UnsupportedQuery", "Item query is unsupported"
                                )
                            return serialize_security(current, parse_security_query(request, kind))
                        query = (
                            None
                            if "$skiptoken" in request.query_params
                            else parse_security_query(request, kind)
                        )
                        source = grant_source(actor.tenant_id, kind)
                        if scope_field:
                            source = (
                                select(source)
                                .where(source.c.payload[scope_field].as_string() == scope_id)
                                .subquery()
                            )
                        return await security_collection(
                            request, session, actor, kind, query, source=source
                        )
                    operation = {"POST": "create", "PATCH": "update", "DELETE": "delete"}[
                        request.method
                    ]
                    if operation == "create" and scope_field and body.get(scope_field) != scope_id:
                        raise GraphError(
                            400,
                            "Request_BadRequest",
                            "Grant reference differs from the relationship",
                        )
                    result = await mutate_grant(
                        session, actor, kind, operation, identifier, body, datetime.now(UTC)
                    )
            except IntegrityError:
                raise GraphError(
                    409, "Request_Conflict", "Permission grant conflicts with existing state"
                ) from None
            return (
                JSONResponse(result, status_code=201)
                if result is not None
                else Response(status_code=204)
            )

        router.add_api_route(path, handle, methods=methods)

    for spelling in ("oauth2PermissionGrants", "oAuth2PermissionGrants"):
        register("/" + spelling, "oAuth2PermissionGrant", ["GET", "POST"])
        register(
            "/" + spelling + "/{identifier}", "oAuth2PermissionGrant", ["GET", "PATCH", "DELETE"]
        )
    for prefix, alternate in (
        ("/servicePrincipals/{principal_id}", False),
        ("/servicePrincipals(appId='{principal_id}')", True),
    ):
        register(
            prefix + "/oauth2PermissionGrants",
            "oAuth2PermissionGrant",
            ["GET"],
            alternate=alternate,
        )
        for relation in ("appRoleAssignments", "appRoleAssignedTo"):
            register(
                prefix + "/" + relation, "appRoleAssignment", ["GET", "POST"], relation, alternate
            )
            register(
                prefix + "/" + relation + "/{identifier}",
                "appRoleAssignment",
                ["GET", "DELETE"],
                relation,
                alternate,
            )
    return router

from uuid import UUID

from fastapi import APIRouter, Request, Response
from starlette.responses import JSONResponse

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import permitted, principal, project
from mockazure.security.method_mutations import require_target_authority
from mockazure.security.methods import PASSWORD_ID, READ_ROLES, method_user
from mockazure.security.password_reset import RESET_ROLES, reconcile_failed, start_password_reset
from mockazure.security.repository import lock_security, missing
from mockazure.security.reset_model import PasswordResetOperation


def router_for(version):
    router = APIRouter(prefix="/" + version)

    async def reset(user_id: str, identifier: str, request: Request):
        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            if version != "v1.0":
                raise GraphError(501, "NotSupported", "Password reset requires the v1.0 profile")
            if identifier != PASSWORD_ID:
                missing()
            if request.query_params:
                raise GraphError(
                    400, "Request_UnsupportedQuery", "Reset query options are unsupported"
                )
            body = await read_json(request, limit=16384)
            if set(body) - {"newPassword"}:
                raise GraphError(400, "Request_BadRequest", "Unsupported password reset field")
            await lock_security(session, actor.tenant_id)
            user = await method_user(session, actor.tenant_id, user_id)
            receipt = await start_password_reset(session, actor, user.id, body.get("newPassword"))
            profile = request.app.state.settings.advertised_endpoints(
                request.headers.get("host", "")
            )
            location = (
                f"{profile.graph}/v1.0/users/{user.id}/authentication/operations/"
                f"{receipt.operation_id}"
            )
            headers = {"Location": location, "Cache-Control": "no-store", "Pragma": "no-cache"}
            if receipt.generated_password is not None:
                return JSONResponse(
                    {"newPassword": receipt.generated_password}, status_code=202, headers=headers
                )
            return Response(status_code=202, headers=headers)

    for collection in ("methods", "passwordMethods"):
        for action in ("resetPassword", "microsoft.graph.resetPassword"):
            router.add_api_route(
                "/users/{user_id}/authentication/" + collection + "/{identifier}/" + action,
                reset,
                methods=["POST"],
            )

    @router.get("/users/{user_id}/authentication/operations/{identifier}")
    async def operation(user_id: str, identifier: str, request: Request):
        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            if version != "v1.0":
                raise GraphError(501, "NotSupported", "Reset operations require the v1.0 profile")
            await lock_security(session, actor.tenant_id)
            user = await method_user(session, actor.tenant_id, user_id)
            try:
                row = await session.get(PasswordResetOperation, UUID(identifier))
            except ValueError:
                row = None
            if row is None or row.tenant_id != actor.tenant_id or row.user_id != user.id:
                missing()
            own = actor.subject_id == user.id
            creator = row.actor_id == actor.subject_id and row.client_id == actor.client_id
            permissions = {
                "UserAuthenticationMethod.Read.All",
                "UserAuthenticationMethod.ReadWrite.All",
            }
            if own:
                permissions |= {
                    "UserAuthenticationMethod.Read",
                    "UserAuthenticationMethod.ReadWrite",
                }
            await permitted(
                actor,
                OperationRequirement(
                    "graph",
                    frozenset(),
                    frozenset(permissions),
                    frozenset() if own else READ_ROLES | (RESET_ROLES if creator else set()),
                ),
                session,
            )
            if creator:
                await require_target_authority(session, actor, user, reset=True)
            await reconcile_failed(session, row)
            profile = request.app.state.settings.advertised_endpoints(
                request.headers.get("host", "")
            )
            return project(
                request,
                {
                    "id": str(row.id),
                    "createdDateTime": row.created_at.isoformat(),
                    "lastActionDateTime": row.updated_at.isoformat(),
                    "status": row.status,
                    "statusDetail": row.status_detail,
                    "resourceLocation": (
                        f"{profile.graph}/v1.0/users/{user.id}/authentication/methods/{PASSWORD_ID}"
                    ),
                },
            )

    return router

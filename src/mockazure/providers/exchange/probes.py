"""Authenticated Exchange bootstrap and fixed command invocation transport."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from mockazure.authorization.evaluator import AuthorizationDenied, OperationRequirement, authorize
from mockazure.identity.tokens import validate_access_token
from mockazure.mail.authorization import require_exchange_command
from mockazure.oauth.errors import OAuthError
from mockazure.providers.exchange.bootstrap import PARAMETERS, fixed_proxy_source
from mockazure.providers.exchange.commands import invoke_command
from mockazure.providers.graph.errors import GraphError

router = APIRouter()
EXCHANGE_ACCESS = OperationRequirement(
    "exchange",
    frozenset({"Exchange.ManageAsApp"}),
    frozenset({"Exchange.Manage"}),
)


async def permitted(request, tenant, session):
    values = request.headers.getlist("authorization")
    scheme, _, raw = values[0].partition(" ") if len(values) == 1 else ("", "", "")
    try:
        if scheme.lower() != "bearer" or not raw:
            raise OAuthError("invalid_token", "Bearer token is required", 401)
        context = await validate_access_token(
            raw,
            "exchange",
            datetime.now(UTC),
            session,
            endpoints=request.app.state.settings.endpoints,
        )
    except OAuthError:
        raise GraphError(
            401, "InvalidAuthenticationToken", "Exchange access token required"
        ) from None
    try:
        if context.tenant_id != UUID(tenant):
            raise AuthorizationDenied()
        await authorize(context, EXCHANGE_ACCESS, session)
    except (AuthorizationDenied, ValueError):
        raise GraphError(
            403, "Authorization_RequestDenied", "Exchange permission required"
        ) from None
    return context


def proxy_source(endpoint):
    return fixed_proxy_source(("Get-OrganizationConfig",), endpoint)


@router.get("/AdminApi/v1.0/{tenant}/EXOModuleFile")
async def module(tenant: str, request: Request):
    async with request.app.state.database.sessions() as session:
        context = await permitted(request, tenant, session)
        if request.query_params.get("Version") != "3.11.0":
            raise GraphError(
                400, "UnsupportedModuleVersion", "Only Exchange module 3.11.0 is qualified"
            )
        requested = request.headers.get("CommandName")
        if requested == "*":
            requested = None
        commands = tuple(requested.split(",")) if requested else tuple(PARAMETERS)
        if any(command not in PARAMETERS for command in commands):
            raise GraphError(400, "UnsupportedCommand", "Requested command is not implemented")
        allowed = []
        for command in commands:
            try:
                await require_exchange_command(session, context, command)
                allowed.append(command)
            except AuthorizationDenied:
                if requested:
                    raise GraphError(
                        403, "Authorization_RequestDenied", "Exchange command permission required"
                    ) from None
        if not allowed:
            raise GraphError(
                403, "Authorization_RequestDenied", "No supported Exchange command permission"
            )
        origin = request.app.state.settings.advertised_endpoints(request.headers.get("host", ""))
        endpoint = f"{origin.exchange}/adminapi/v2.0/{context.tenant_id}/InvokeCommand"
        return {
            "value": [
                {
                    "fileName": "MockAzure.psm1",
                    "fileContent": fixed_proxy_source(tuple(allowed), endpoint),
                    "exportedCmdlets": ",".join(allowed),
                }
            ]
        }


class CmdletInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    CmdletName: str
    Parameters: dict


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    CmdletInput: CmdletInput


@router.post("/adminapi/v2.0/{tenant}/InvokeCommand")
@router.post("/adminapi/beta/{tenant}/InvokeCommand")
async def invoke(tenant: str, data: CommandRequest, request: Request):

    now = getattr(request.app.state, "clock", lambda: datetime.now(UTC))()
    try:
        name = data.CmdletInput.CmdletName
        trace_name = next(
            (
                known
                for known in ("Get-MessageTraceV2", "Get-MessageTraceDetailV2")
                if known.lower() == name.lower()
            ),
            None,
        )
        if trace_name:
            from mockazure.mail.trace import consume_trace_budget

            async with request.app.state.database.sessions.begin() as session:
                context = await permitted(request, tenant, session)
                retry = await consume_trace_budget(session, context, trace_name, now)
            if retry is not None:
                error = GraphError(429, "TooManyRequests", "Exchange trace query limit reached")
                error.headers["Retry-After"] = str(retry)
                raise error
        async with request.app.state.database.sessions.begin() as session:
            context = await permitted(request, tenant, session)
            rows = await invoke_command(
                session,
                context,
                data.CmdletInput.CmdletName,
                data.CmdletInput.Parameters,
                now,
            )
            return {"value": rows}
    except AuthorizationDenied:
        raise GraphError(
            403, "Authorization_RequestDenied", "Exchange command permission required"
        ) from None

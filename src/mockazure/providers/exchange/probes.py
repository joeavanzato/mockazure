from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from mockazure.authorization.evaluator import AuthorizationDenied, OperationRequirement, authorize
from mockazure.db.models import Tenant
from mockazure.identity.tokens import validate_access_token
from mockazure.oauth.errors import OAuthError
from mockazure.providers.graph.errors import GraphError

router = APIRouter()
ORGANIZATION = OperationRequirement(
    "exchange",
    frozenset({"Exchange.ManageAsApp"}),
    frozenset({"Exchange.Manage"}),
    frozenset({"Global Administrator", "Global Reader"}),
    application_roles=frozenset({"Global Administrator", "Global Reader"}),
)


async def permitted(request, tenant, session):
    scheme, _, raw = request.headers.get("authorization", "").partition(" ")
    try:
        if scheme.lower() != "bearer":
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
        await authorize(context, ORGANIZATION, session)
    except (AuthorizationDenied, ValueError):
        raise GraphError(
            403, "Authorization_RequestDenied", "Exchange permission required"
        ) from None
    return context


def proxy_source(endpoint):
    # The server returns code only for this fixed command. Never interpolate credentials,
    # user data, arbitrary command names or arbitrary command arguments into source.
    endpoint = endpoint.replace("'", "''")
    return (
        "function Get-OrganizationConfig {\n"
        "    [CmdletBinding()] param()\n"
        "    $context = [Microsoft.Exchange.Management.ExoPowershellSnapin."
        "ConnectionContextFactory]::GetCurrentConnectionContext($script:ConnectionContextObjectId)\n"
        "    $auth = $context.GetAuthHeader()\n"
        "    $body = @{ CmdletInput = @{ CmdletName = 'Get-OrganizationConfig'; "
        "Parameters = @{} } } | ConvertTo-Json -Depth 4\n"
        "    $result = Invoke-RestMethod -Method POST -Uri '" + endpoint + "' "
        "-ContentType 'application/json' -Headers @{ Authorization = $auth.AuthorizationHeader } "
        "-Body $body -ErrorAction Stop\n"
        "    $result.value\n"
        "}\nExport-ModuleMember -Function Get-OrganizationConfig\n"
    )


@router.get("/AdminApi/v1.0/{tenant}/EXOModuleFile")
async def module(tenant: str, request: Request):
    async with request.app.state.database.sessions() as session:
        context = await permitted(request, tenant, session)
        if request.query_params.get("Version") != "3.11.0":
            raise GraphError(
                400, "UnsupportedModuleVersion", "Only Exchange module 3.11.0 is qualified"
            )
        commands = request.headers.get("CommandName", "Get-OrganizationConfig").split(",")
        if set(commands) != {"Get-OrganizationConfig"}:
            raise GraphError(
                400, "UnsupportedCommand", "Only Get-OrganizationConfig is implemented"
            )
        origin = request.app.state.settings.advertised_endpoints(request.headers.get("host", ""))
        endpoint = f"{origin.exchange}/adminapi/v2.0/{context.tenant_id}/InvokeCommand"
        return {
            "value": [
                {
                    "fileName": "MockAzure.psm1",
                    "fileContent": proxy_source(endpoint),
                    "exportedCmdlets": "Get-OrganizationConfig",
                }
            ]
        }


class CmdletInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    CmdletName: str
    Parameters: dict


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    CmdletInput: CmdletInput


@router.post("/adminapi/v2.0/{tenant}/InvokeCommand")
async def invoke(tenant: str, data: CommandRequest, request: Request):
    async with request.app.state.database.sessions() as session:
        context = await permitted(request, tenant, session)
        if data.CmdletInput.CmdletName != "Get-OrganizationConfig" or data.CmdletInput.Parameters:
            raise GraphError(
                400,
                "UnsupportedCommand",
                "Only parameterless Get-OrganizationConfig is implemented",
            )
        row = await session.get(Tenant, context.tenant_id)
        return {
            "value": [
                {
                    "Identity": str(row.id),
                    "Name": row.display_name,
                    "Guid": str(row.id),
                    "ExternalDirectoryOrganizationId": str(row.id),
                }
            ]
        }

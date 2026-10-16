from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from mockazure.authorization.catalog import DIRECTORY_ROLES
from mockazure.authorization.evaluator import AuthorizationDenied, OperationRequirement, authorize
from mockazure.db.models import Tenant
from mockazure.identity.models import User
from mockazure.identity.tokens import validate_access_token
from mockazure.oauth.errors import OAuthError
from mockazure.providers.graph.errors import GraphError

router = APIRouter()
ORGANIZATION = OperationRequirement(
    "graph",
    frozenset({"Organization.Read.All", "Directory.Read.All"}),
    frozenset({"User.Read", "Organization.Read.All", "Directory.Read.All"}),
    DIRECTORY_ROLES,
    "organization",
)
ME = OperationRequirement("graph", frozenset(), frozenset({"User.Read"}), access_rule="self")


async def principal(request: Request, session):
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        raise GraphError(401, "InvalidAuthenticationToken", "A resource access token is required")
    scheme, _, raw = values[0].partition(" ")
    if scheme.lower() != "bearer" or not raw:
        raise GraphError(401, "InvalidAuthenticationToken", "A resource access token is required")
    try:
        return await validate_access_token(
            raw, "graph", datetime.now(UTC), session, endpoints=request.app.state.settings.endpoints
        )
    except OAuthError:
        raise GraphError(401, "InvalidAuthenticationToken", "Access token is invalid") from None


def project(request, data):
    params = list(request.query_params.multi_items())
    if not params:
        return data
    if len(params) != 1 or params[0][0] != "$select":
        raise GraphError(400, "Request_UnsupportedQuery", "Query option is not supported here")
    selected = params[0][1].split(",")
    if not selected or len(selected) > len(data) or any(key not in data for key in selected):
        raise GraphError(400, "Request_UnsupportedQuery", "Selected field is not supported")
    return {key: data[key] for key in selected}


async def permitted(context, operation, session):
    try:
        await authorize(context, operation, session)
    except AuthorizationDenied:
        raise GraphError(
            403, "Authorization_RequestDenied", "Insufficient privileges to complete the operation"
        ) from None


def context_url(request, resource):
    version = request.url.path.split("/")[1]
    profile = request.app.state.settings.advertised_endpoints(request.headers.get("host", ""))
    return f"{profile.graph}/{version}/$metadata#{resource}"


@router.get("/organization")
async def organization(request: Request, response: Response):
    async with request.app.state.database.sessions() as session:
        context = await principal(request, session)
        await permitted(context, ORGANIZATION, session)
        tenant = await session.get(Tenant, context.tenant_id)
        data = {
            "id": str(tenant.id),
            "displayName": tenant.display_name,
            "verifiedDomains": [
                {"name": tenant.domain, "isDefault": True, "isInitial": True, "type": "Managed"}
            ],
        }
        response.headers["request-id"] = request.state.request_id
        return {
            "@odata.context": context_url(request, "organization"),
            "value": [project(request, data)],
        }


@router.get("/me")
async def me(request: Request, response: Response):
    async with request.app.state.database.sessions() as session:
        context = await principal(request, session)
        if context.token_kind == "application":
            raise GraphError(400, "BadRequest", "/me requires delegated authentication")
        await permitted(context, ME, session)
        user = await session.scalar(
            select(User).where(User.tenant_id == context.tenant_id, User.id == context.subject_id)
        )
        data = {
            "id": str(user.id),
            "displayName": user.display_name,
            "userPrincipalName": user.upn,
            "mail": None,
        }
        response.headers["request-id"] = request.state.request_id
        return {"@odata.context": context_url(request, "users/$entity"), **project(request, data)}

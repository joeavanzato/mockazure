"""Current Azure data actions never inherit control-plane actions."""

from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.identity.types import PrincipalContext
from mockazure.providers.arm.authorization import grant_query
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import resolve_scope
from mockazure.providers.arm.models import RoleAssignment
from mockazure.providers.arm.scopes import parse_scope


async def require_data_action(
    session: AsyncSession, actor: PrincipalContext, scope: str, action: str
) -> None:
    prefixes = {
        "microsoft.storage/storageaccounts/": "https://storage.azure.com/",
        "microsoft.keyvault/vaults/": "https://vault.azure.net",
    }
    audience = next(
        (value for prefix, value in prefixes.items() if action.lower().startswith(prefix)), None
    )
    if audience is None:
        raise ArmError(403, "AuthorizationFailed", "Unsupported data service action")
    try:
        resource = parse_scope(scope)
    except ValueError:
        raise ArmError(400, "InvalidResourceId", "Invalid data permission scope") from None
    await resolve_scope(session, actor.tenant_id, resource)
    query = await grant_query(session, actor, action, audience=audience, data=True)
    allowed = await session.scalar(
        query.where(RoleAssignment.scope_key.in_((*resource.parent_keys, resource.key))).limit(1)
    )
    if allowed is None:
        raise ArmError(403, "AuthorizationFailed", "Current identity lacks scoped data access")

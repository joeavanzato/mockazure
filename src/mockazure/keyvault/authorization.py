"""Current vault mode decides authority; ARM management never implies secret access."""

from mockazure.keyvault.models import Vault
from mockazure.providers.arm.authorization import current_principal, grant_query, security_groups
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.models import ArmResource, RoleAssignment, Subscription
from mockazure.providers.arm.scopes import parse_scope
from mockazure.providers.keyvault.errors import VaultError
from mockazure.storage.accounts import network_allowed

AUDIENCE = "https://vault.azure.net"
PERMISSIONS = {
    "get": "getSecret/action",
    "list": "readMetadata/action",
    "versions": "readMetadata/action",
    "set": "setSecret/action",
    "update": "setSecret/action",
    "delete": "delete",
    "recover": "recover/action",
    "purge": "purge/action",
    "backup": "backup/action",
    "restore": "restore/action",
    "list_deleted": "readMetadata/action",
    "get_deleted": "getSecret/action",
}
POLICIES = {"versions": "list", "update": "set", "list_deleted": "list", "get_deleted": "get"}


async def vault_configuration(session, tenant, vault_id):
    vault = await session.get(Vault, (tenant, vault_id), populate_existing=True)
    row = await session.get(ArmResource, (tenant, vault_id), populate_existing=True)
    if vault is None or row is None or row.provisioning_state != "Succeeded":
        raise VaultError(404, "VaultNotFound", "The vault is unavailable")
    scope = parse_scope(vault_id)
    subscription = await session.get(Subscription, scope.subscription_id, populate_existing=True)
    if subscription is None or subscription.tenant_id != tenant or subscription.state != "Enabled":
        raise VaultError(403, "Forbidden", "The subscription is unavailable")
    return vault, row.payload["properties"]


async def require_secret_access(session, actor, vault_id, secret_name, operation):
    _, props = await vault_configuration(session, actor.tenant_id, vault_id)
    try:
        principal = await current_principal(session, actor, audience=AUDIENCE)
    except ArmError:
        raise VaultError(403, "Forbidden", "Current identity is unavailable") from None
    if not network_allowed(props, session.info.get("keyvault_peer"), {}):
        raise VaultError(403, "Forbidden", "Current vault network policy denies access")
    if operation not in PERMISSIONS:
        raise VaultError(501, "NotImplemented", "Unsupported secret operation")
    if props.get("enableRbacAuthorization", False):
        scope = parse_scope(vault_id)
        scopes = [*scope.parent_keys, scope.key]
        if secret_name:
            scopes.append(scope.key + "/secrets/" + secret_name.lower())
        try:
            query = await grant_query(
                session,
                actor,
                "Microsoft.KeyVault/vaults/secrets/" + PERMISSIONS[operation],
                audience=AUDIENCE,
                data=True,
            )
        except ArmError:
            raise VaultError(403, "Forbidden", "Current identity is unavailable") from None
        if await session.scalar(query.where(RoleAssignment.scope_key.in_(scopes)).limit(1)):
            return
    else:
        principals = {str(principal)} | {
            str(v) for v in await session.scalars(security_groups(actor.tenant_id, principal))
        }
        for policy in props.get("accessPolicies", []):
            if (
                str(policy.get("tenantId", "")).lower() != str(actor.tenant_id)
                or str(policy.get("objectId", "")).lower() not in principals
            ):
                continue
            permissions = policy.get("permissions", {}).get("secrets", [])
            if "all" in permissions or POLICIES.get(operation, operation) in permissions:
                return
    raise VaultError(403, "Forbidden", "Current identity lacks the required secret permission")

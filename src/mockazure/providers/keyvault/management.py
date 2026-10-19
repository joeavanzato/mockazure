"""Official ARM deleted-vault lookup and synchronous purge contracts."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from sqlalchemy import select, text

from mockazure.keyvault.secret_models import DeletedVault
from mockazure.keyvault.vault_recovery import purge_vault
from mockazure.providers.arm.authorization import require_action
from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory_routes import request_scope
from mockazure.providers.arm.provider_registry import resolve_operation
from mockazure.providers.arm.provider_routes import provider_transaction
from mockazure.providers.arm.resource_handlers import publish
from mockazure.providers.arm.scopes import parse_scope

router = APIRouter()


def deleted_view(row):
    subscription = parse_scope(row.vault_id).subscription_id
    return {
        "id": (
            f"/subscriptions/{subscription}/providers/Microsoft.KeyVault"
            f"/locations/{row.location}/deletedVaults/{row.name}"
        ),
        "name": row.name,
        "type": "Microsoft.KeyVault/locations/deletedVaults",
        "properties": {
            "vaultId": row.display_id,
            "location": row.location,
            "deletionDate": row.deleted_at.isoformat(),
            "scheduledPurgeDate": row.purge_at.isoformat(),
            "tags": row.tags,
            "purgeProtectionEnabled": row.payload["properties"].get("enablePurgeProtection", False),
        },
    }


async def handle(request, subscription, location=None, name=None):
    contract = resolve_operation(
        request.method, request.url.path, request.query_params.get("api-version")
    )
    if len(request.query_params.multi_items()) != 1 or set(request.query_params) != {"api-version"}:
        raise ArmError(400, "InvalidQueryParameterValue", "Unsupported deleted-vault query")
    if await request.body():
        raise ArmError(
            400, "InvalidRequestContent", "Deleted-vault operations require an empty body"
        )
    scope = request_scope(subscription)
    async with provider_transaction(request, scope, mutating=True) as (session, actor, sub):
        await require_action(session, actor, scope, contract["permissions"][0])
        await session.execute(text("SELECT pg_advisory_xact_lock(726385210059)"))
        now = datetime.now(UTC)
        query = select(DeletedVault).where(
            DeletedVault.tenant_id == actor.tenant_id,
            DeletedVault.vault_id.startswith(scope.key + "/", autoescape=True),
            DeletedVault.purge_at > now,
        )
        if name is None:
            rows = list(await session.scalars(query.order_by(DeletedVault.vault_id).limit(10001)))
            if len(rows) > 10000:
                raise ArmError(
                    413, "ResourceLimitExceeded", "Deleted vault list exceeds local bound"
                )
            result = {"value": [deleted_view(row) for row in rows]}
            validate_arm_payload("DeletedVaultListResult", result, namespace="vaults")
            return result
        row = await session.scalar(
            query.where(
                DeletedVault.name == name.lower(), DeletedVault.location == location.lower()
            )
        )
        if row is None:
            raise ArmError(404, "VaultNotFound", "Deleted vault was not found")
        result = deleted_view(row)
        if request.method == "POST":
            if row.payload["properties"].get("enablePurgeProtection"):
                raise ArmError(403, "Forbidden", "Purge protection is enabled")
            await publish(
                session,
                actor,
                parse_scope(row.display_id),
                contract["permissions"][0],
                result,
                {},
                now,
            )
            await purge_vault(session, row)
            return Response(status_code=200)
        validate_arm_payload("DeletedVault", result, namespace="vaults")
        return result


@router.get("/subscriptions/{subscription}/providers/Microsoft.KeyVault/deletedVaults")
async def listed(request: Request, subscription: str):
    return await handle(request, subscription)


@router.get(
    "/subscriptions/{subscription}/providers/Microsoft.KeyVault/locations/{location}/deletedVaults/{name}"
)
async def get_deleted(request: Request, subscription: str, location: str, name: str):
    return await handle(request, subscription, location, name)


@router.post(
    "/subscriptions/{subscription}/providers/Microsoft.KeyVault/locations/{location}/deletedVaults/{name}/purge"
)
async def purge_deleted(request: Request, subscription: str, location: str, name: str):
    return await handle(request, subscription, location, name)

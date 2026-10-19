"""Retain vault configuration and encrypted contents across ARM deletion."""

import copy
from datetime import timedelta

from sqlalchemy import delete, select, text

from mockazure.keyvault.secret_models import DeletedVault, Secret
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.models import ArmResource, RoleAssignment
from mockazure.providers.arm.resource_validation import configuration, profile_for


async def archive_vaults(session, tenant, keys, now):
    await session.execute(text("SELECT pg_advisory_xact_lock(726385210059)"))
    rows = list(
        await session.scalars(
            select(ArmResource).where(
                ArmResource.tenant_id == tenant,
                ArmResource.scope_key.in_(keys),
                ArmResource.resource_type == "Microsoft.KeyVault/vaults",
            )
        )
    )
    for row in rows:
        value = configuration(row, profile_for(row.resource_type)[1])
        session.add(
            DeletedVault(
                tenant_id=tenant,
                vault_id=row.scope_key,
                name=row.name.lower(),
                display_id=row.display_id,
                location=row.location,
                tags=copy.deepcopy(row.tags),
                payload={k: v for k, v in value.items() if k not in {"location", "tags"}},
                deleted_at=now,
                purge_at=now + timedelta(days=value["properties"]["softDeleteRetentionInDays"]),
            )
        )
        await session.execute(
            delete(RoleAssignment).where(
                RoleAssignment.tenant_id == tenant,
                (RoleAssignment.scope_key == row.scope_key)
                | RoleAssignment.scope_key.startswith(row.scope_key + "/", autoescape=True),
            )
        )
    await session.flush()


async def purge_vault(session, row):
    await session.execute(
        delete(Secret).where(Secret.tenant_id == row.tenant_id, Secret.vault_id == row.vault_id)
    )
    await session.delete(row)
    await session.flush()


async def recovery_configuration(session, tenant, scope, body, now):
    await session.execute(text("SELECT pg_advisory_xact_lock(726385210059)"))
    row = await session.scalar(
        select(DeletedVault).where(DeletedVault.name == scope.resource_parts[-1].lower())
    )
    if row is not None and row.purge_at <= now:
        await purge_vault(session, row)
        row = None
    mode = body.get("properties", {}).get("createMode", "default")
    if mode not in {"default", "recover"}:
        raise ValueError("Unsupported vault createMode")
    if mode == "recover":
        if row is None or row.tenant_id != tenant or row.vault_id != scope.key:
            raise ArmError(404, "VaultNotFound", "Recoverable vault was not found at this scope")
        if body.get("location", "").lower() != row.location.lower():
            raise ValueError("Recovered vault location must match")
        value = copy.deepcopy(row.payload) | {
            "location": row.location,
            "tags": copy.deepcopy(row.tags),
        }
        await session.delete(row)
        await session.flush()
        return value
    if row is not None:
        raise ArmError(409, "VaultAlreadyExists", "Vault name is retained by soft delete")
    result = copy.deepcopy(body)
    result.get("properties", {}).pop("createMode", None)
    return result

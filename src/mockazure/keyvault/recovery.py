"""Recover all secret versions; protection prevents premature permanent deletion."""

from datetime import timedelta

from sqlalchemy import select

from mockazure.keyvault.authorization import require_secret_access, vault_configuration
from mockazure.keyvault.contracts import name_key
from mockazure.keyvault.endpoints import vault_origin
from mockazure.keyvault.secret_models import Secret
from mockazure.keyvault.secrets import find_secret, find_version, record, view
from mockazure.providers.keyvault.errors import VaultError


def deleted_view(session, vault, props, secret, version, *, versioned=True):
    return view(session, vault, props, secret, version, versioned=versioned) | {
        "recoveryId": vault_origin(session.info["keyvault_endpoint"], vault.name)
        + "/deletedsecrets/"
        + secret.name,
        "deletedDate": int(secret.deleted_at.timestamp()),
        "scheduledPurgeDate": int(secret.purge_at.timestamp()),
    }


async def expired_name(session, tenant, vault_id, name, now):
    row = await session.scalar(
        select(Secret).where(
            Secret.tenant_id == tenant,
            Secret.vault_id == vault_id,
            Secret.name_key == name_key(name),
        )
    )
    if row and row.deleted_at and row.purge_at <= now:
        await session.delete(row)
        await session.flush()


async def get_deleted_secret(session, actor, vault_id, name, now):
    await require_secret_access(session, actor, vault_id, name, "get_deleted")
    await expired_name(session, actor.tenant_id, vault_id, name, now)
    secret = await find_secret(session, actor.tenant_id, vault_id, name, deleted=True)
    row = await find_version(session, secret, None)
    vault, props = await vault_configuration(session, actor.tenant_id, vault_id)
    return deleted_view(session, vault, props, secret, row)


async def mutate_recovery(session, actor, vault_id, name, operation, now):
    await expired_name(session, actor.tenant_id, vault_id, name, now)
    secret = await find_secret(
        session, actor.tenant_id, vault_id, name, deleted=operation != "delete"
    )
    row = await find_version(session, secret, None)
    vault, props = await vault_configuration(session, actor.tenant_id, vault_id)
    if operation == "delete":
        secret.deleted_at = now
        secret.purge_at = now + timedelta(days=props.get("softDeleteRetentionInDays", 90))
        result = deleted_view(session, vault, props, secret, row)
    elif operation == "recover":
        secret.deleted_at = secret.purge_at = None
        result = view(session, vault, props, secret, row)
    elif operation == "purge":
        if props.get("enablePurgeProtection"):
            raise VaultError(403, "Forbidden", "Purge protection is enabled")
        await session.delete(secret)
        result = None
    else:
        raise VaultError(501, "NotImplemented", "Unsupported recovery operation")
    await record(
        session,
        actor,
        vault_id,
        secret.name,
        {"delete": "SecretDelete", "recover": "SecretRecover", "purge": "SecretPurge"}[operation],
        now,
    )
    await session.flush()
    return result

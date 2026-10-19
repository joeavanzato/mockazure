"""Explicit portable secret backups use a separate operator-provided key reference."""

from pathlib import Path
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from pydantic import Field, ValidationError, model_validator
from sqlalchemy import select

from mockazure.identity.types import IdentityInput
from mockazure.keyvault.authorization import require_secret_access, vault_configuration
from mockazure.keyvault.contracts import checked_body, validate_shape
from mockazure.keyvault.lifecycle import SecretVersionInput
from mockazure.keyvault.recovery import expired_name
from mockazure.keyvault.secret_models import Secret, SecretVersion
from mockazure.keyvault.secrets import find_secret, find_version, record, seal, unseal, view
from mockazure.providers.arm.models import ArmResource
from mockazure.providers.arm.scopes import parse_scope
from mockazure.providers.keyvault.errors import VaultError


class BackupVersion(SecretVersionInput):
    value: str

    @model_validator(mode="after")
    def checked_value(self):
        try:
            checked_body({"value": self.value}, "set")
        except VaultError:
            raise ValueError("Invalid backup value") from None
        return self


class SecretBackup(IdentityInput):
    format: str
    manifest_version: int = Field(strict=True, ge=1, le=1)
    key_ref: str = Field(min_length=1, max_length=128)
    tenant_id: UUID
    subscription_id: UUID
    geography: str
    name: str
    versions: list[BackupVersion] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def checked(self):
        from mockazure.keyvault.contracts import name_key

        try:
            name_key(self.name)
        except VaultError:
            raise ValueError("Invalid backup name") from None
        if (
            self.format != "mockazure-keyvault-secret"
            or len({v.version for v in self.versions}) != len(self.versions)
            or len({v.sequence for v in self.versions}) != len(self.versions)
        ):
            raise ValueError("Invalid backup manifest")
        return self


def backup_cipher(session):
    reference = session.info.get("keyvault_backup_key_ref")
    path = session.info.get("keyvault_backup_key_file")
    if not reference or not path:
        raise VaultError(
            409, "BackupKeyRequired", "An explicit backup key file and reference must be configured"
        )
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 128:
            raise ValueError()
        return Fernet(raw.strip()), reference
    except (ValueError, OSError):
        raise VaultError(
            503, "ServiceUnavailable", "Configured backup key is unavailable"
        ) from None


async def geography(session, tenant, vault_id):
    row = await session.get(ArmResource, (tenant, vault_id))
    # These are the two locations in the pinned local Key Vault provider profile.
    if row.location.lower() not in {"eastus", "westus2"}:
        raise VaultError(501, "NotImplemented", "Backup geography is not modeled for this location")
    return "United States"


async def backup_secret(session, actor, vault_id, name, now):
    await require_secret_access(session, actor, vault_id, name, "backup")
    cipher, reference = backup_cipher(session)
    secret = await find_secret(session, actor.tenant_id, vault_id, name)
    rows = list(
        await session.scalars(
            select(SecretVersion)
            .where(SecretVersion.tenant_id == actor.tenant_id, SecretVersion.secret_id == secret.id)
            .order_by(SecretVersion.sequence)
            .limit(501)
        )
    )
    if len(rows) > 500:
        raise VaultError(400, "BadParameter", "Backup supports at most 500 versions")
    manifest = SecretBackup(
        format="mockazure-keyvault-secret",
        manifest_version=1,
        key_ref=reference,
        tenant_id=actor.tenant_id,
        subscription_id=parse_scope(vault_id).subscription_id,
        geography=await geography(session, actor.tenant_id, vault_id),
        name=secret.name,
        versions=[
            BackupVersion(
                version=r.version,
                sequence=r.sequence,
                attributes=r.attributes,
                tags=r.tags,
                content_type=r.content_type,
                created_at=r.created_at,
                updated_at=r.updated_at,
                value=unseal(session, secret, r),
            )
            for r in rows
        ],
    )
    token = cipher.encrypt(manifest.model_dump_json().encode()).decode("ascii")
    await record(session, actor, vault_id, secret.name, "SecretBackup", now)
    return {"value": token}


async def restore_secret(session, actor, vault_id, body, now):
    await require_secret_access(session, actor, vault_id, None, "restore")
    validate_shape("SecretRestoreParameters", body, writable=True)
    cipher, reference = backup_cipher(session)
    try:
        token = body["value"].encode("ascii")
        if len(token) > 32 * 1024 * 1024:
            raise ValueError()
        manifest = SecretBackup.model_validate_json(cipher.decrypt(token))
    except (InvalidToken, ValueError, UnicodeError, ValidationError):
        raise VaultError(400, "BadParameter", "Invalid encrypted secret backup") from None
    if manifest.key_ref != reference:
        raise VaultError(400, "BadParameter", "Backup key reference does not match")
    if (
        manifest.tenant_id != actor.tenant_id
        or manifest.subscription_id != parse_scope(vault_id).subscription_id
        or manifest.geography != await geography(session, actor.tenant_id, vault_id)
    ):
        raise VaultError(
            403, "Forbidden", "Backup belongs to a different tenant, subscription or geography"
        )
    await expired_name(session, actor.tenant_id, vault_id, manifest.name, now)
    existing = await session.scalar(
        select(Secret.id).where(
            Secret.tenant_id == actor.tenant_id,
            Secret.vault_id == vault_id,
            Secret.name_key == manifest.name.lower(),
        )
    )
    if existing:
        raise VaultError(409, "Conflict", "A secret with this name already exists")
    secret = Secret(
        tenant_id=actor.tenant_id,
        vault_id=vault_id,
        name=manifest.name,
        name_key=manifest.name.lower(),
        revision=max(v.sequence for v in manifest.versions),
    )
    session.add(secret)
    await session.flush()
    for value in manifest.versions:
        row = SecretVersion(
            tenant_id=actor.tenant_id,
            id=uuid4(),
            secret_id=secret.id,
            **value.model_dump(exclude={"value"}),
        )
        row.encrypted_value = seal(session, secret, row, value.value)
        session.add(row)
    await session.flush()
    vault, props = await vault_configuration(session, actor.tenant_id, vault_id)
    latest = await find_version(session, secret, None)
    await record(session, actor, vault_id, secret.name, "SecretRestore", now)
    return view(session, vault, props, secret, latest)

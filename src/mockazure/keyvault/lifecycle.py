"""Default cloud fixtures contain secret metadata only, never value ciphertext."""

import re
from datetime import datetime

from pydantic import Field, model_validator
from sqlalchemy import delete, select

from mockazure.admin.errors import AdminError
from mockazure.identity.types import IdentityInput
from mockazure.keyvault.contracts import checked_body, name_key
from mockazure.keyvault.models import Vault
from mockazure.keyvault.secret_models import DeletedVault, Secret, SecretCursor, SecretVersion
from mockazure.providers.arm.scopes import parse_scope
from mockazure.providers.keyvault.errors import VaultError


class SecretVersionInput(IdentityInput):
    version: str
    sequence: int = Field(ge=1, le=9223372036854775807)
    attributes: dict
    tags: dict = Field(default_factory=dict)
    content_type: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def checked(self):
        if not re.fullmatch(r"[0-9a-f]{32}", self.version):
            raise ValueError("Invalid secret version")
        if (
            self.created_at.tzinfo is None
            or self.updated_at.tzinfo is None
            or self.updated_at < self.created_at
        ):
            raise ValueError("Invalid secret timestamps")
        body = {"attributes": self.attributes, "tags": self.tags}
        if self.content_type is not None:
            body["contentType"] = self.content_type
        try:
            checked_body(body, "update")
        except VaultError:
            raise ValueError("Invalid secret metadata") from None
        return self


class SecretInput(IdentityInput):
    vault_id: str
    name: str
    deleted_at: datetime | None = None
    purge_at: datetime | None = None
    versions: list[SecretVersionInput] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def checked(self):
        scope = parse_scope(self.vault_id)
        if len(scope.resource_parts) != 4 or tuple(
            p.lower() for p in scope.resource_parts[1:3]
        ) != ("microsoft.keyvault", "vaults"):
            raise ValueError("Vault resource ID required")
        self.vault_id = scope.key
        try:
            name_key(self.name)
        except VaultError:
            raise ValueError("Invalid secret name") from None
        if len({v.version for v in self.versions}) != len(self.versions) or len(
            {v.sequence for v in self.versions}
        ) != len(self.versions):
            raise ValueError("Duplicate secret versions")
        if (self.deleted_at is None) != (self.purge_at is None):
            raise ValueError("Incomplete deleted secret state")
        if self.deleted_at is not None and (
            self.deleted_at.tzinfo is None
            or self.purge_at.tzinfo is None
            or self.purge_at <= self.deleted_at
        ):
            raise ValueError("Invalid recovery timeline")
        return self


class DeletedVaultInput(IdentityInput):
    vault_id: str
    name: str
    display_id: str
    location: str
    tags: dict
    payload: dict
    deleted_at: datetime
    purge_at: datetime

    @model_validator(mode="after")
    def checked(self):
        scope = parse_scope(self.vault_id)
        if (
            len(scope.resource_parts) != 4
            or tuple(p.lower() for p in scope.resource_parts[1:3])
            != ("microsoft.keyvault", "vaults")
            or scope.key != parse_scope(self.display_id).key
            or self.name != scope.resource_parts[-1].lower()
        ):
            raise ValueError("Invalid deleted vault identity")
        if (
            self.deleted_at.tzinfo is None
            or self.purge_at.tzinfo is None
            or self.purge_at <= self.deleted_at
        ):
            raise ValueError("Invalid vault recovery timeline")
        self.vault_id = scope.key
        from mockazure.providers.arm.provider_registry import resolve_operation
        from mockazure.providers.arm.resource_validation import profile_for, validate_wire

        profile = profile_for("Microsoft.KeyVault/vaults")[1]
        validate_wire(
            self.payload | {"location": self.location, "tags": self.tags},
            resolve_operation("PUT", self.display_id, profile["apiVersion"]),
            profile,
        )
        if "createMode" in self.payload.get("properties", {}):
            raise ValueError("Snapshot vault cannot contain createMode")
        return self


class KeyVaultStateInput(IdentityInput):
    deleted_vaults: list[DeletedVaultInput] = Field(default_factory=list, max_length=10000)
    secrets: list[SecretInput] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def checked(self):
        if len({(s.vault_id, s.name.lower()) for s in self.secrets}) != len(self.secrets):
            raise ValueError("Duplicate secret name")
        if len({v.vault_id for v in self.deleted_vaults}) != len(self.deleted_vaults) or len(
            {v.name for v in self.deleted_vaults}
        ) != len(self.deleted_vaults):
            raise ValueError("Duplicate deleted vault")
        versions = [v.version for s in self.secrets for v in s.versions]
        if len(versions) > 100000:
            raise ValueError("Invalid or excessive secret versions")
        return self


async def export_keyvault(session, tenant):
    rows = list(
        await session.scalars(
            select(Secret)
            .where(Secret.tenant_id == tenant)
            .order_by(Secret.vault_id, Secret.name_key)
            .limit(10001)
        )
    )
    if len(rows) > 10000:
        raise AdminError(413, "CloudStateLimit", "Too many secrets in metadata snapshot")
    output = []
    count = 0
    for row in rows:
        versions = list(
            await session.scalars(
                select(SecretVersion)
                .where(SecretVersion.tenant_id == tenant, SecretVersion.secret_id == row.id)
                .order_by(SecretVersion.sequence)
                .limit(1001)
            )
        )
        count += len(versions)
        if len(versions) > 1000 or count > 100000:
            raise AdminError(
                413, "CloudStateLimit", "Too many secret versions in metadata snapshot"
            )
        output.append(
            SecretInput(
                vault_id=row.vault_id,
                name=row.name,
                deleted_at=row.deleted_at,
                purge_at=row.purge_at,
                versions=[
                    SecretVersionInput(
                        version=v.version,
                        sequence=v.sequence,
                        attributes=v.attributes,
                        tags=v.tags,
                        content_type=v.content_type,
                        created_at=v.created_at,
                        updated_at=v.updated_at,
                    )
                    for v in versions
                ],
            )
        )
    deleted_rows = list(
        await session.scalars(
            select(DeletedVault)
            .where(DeletedVault.tenant_id == tenant)
            .order_by(DeletedVault.vault_id)
            .limit(10001)
        )
    )
    if len(deleted_rows) > 10000:
        raise AdminError(413, "CloudStateLimit", "Too many deleted vaults")
    deleted_vaults = [
        DeletedVaultInput(**{key: getattr(row, key) for key in DeletedVaultInput.model_fields})
        for row in deleted_rows
    ]
    return KeyVaultStateInput(secrets=output, deleted_vaults=deleted_vaults)


async def replace_keyvault(session, tenant, state):
    from sqlalchemy import text

    from mockazure.keyvault.vaults import validate_vault
    from mockazure.providers.arm.inventory import checked_tags
    from mockazure.providers.arm.models import Subscription

    await session.execute(text("SELECT pg_advisory_xact_lock(726385210059)"))
    deleted_ids = {item.vault_id for item in state.deleted_vaults}
    for item in state.deleted_vaults:
        scope = parse_scope(item.vault_id)
        sub = await session.get(Subscription, scope.subscription_id)
        if (
            sub is None
            or sub.tenant_id != tenant
            or await session.scalar(select(Vault.name).where(Vault.name == item.name))
        ):
            raise AdminError(422, "InvalidCloudState", "Deleted vault ownership conflicts")
        other = await session.scalar(
            select(DeletedVault).where(
                DeletedVault.name == item.name, DeletedVault.tenant_id != tenant
            )
        )
        if other is not None:
            raise AdminError(422, "InvalidCloudState", "Deleted vault name is unavailable")
        await validate_vault(session, tenant, item.payload["properties"])
        checked_tags(item.tags)
        if (
            item.location not in {"eastus", "westus2"}
            or (item.purge_at - item.deleted_at).total_seconds()
            != item.payload["properties"]["softDeleteRetentionInDays"] * 86400
        ):
            raise AdminError(
                422, "InvalidCloudState", "Invalid deleted vault retention or location"
            )
    for item in state.secrets:
        if (
            item.vault_id not in deleted_ids
            and await session.get(Vault, (tenant, item.vault_id)) is None
        ):
            raise AdminError(422, "InvalidCloudState", "Secret vault is not owned by this tenant")
    await session.execute(delete(DeletedVault).where(DeletedVault.tenant_id == tenant))
    for item in state.deleted_vaults:
        session.add(DeletedVault(tenant_id=tenant, **item.model_dump()))
    await session.execute(delete(SecretCursor).where(SecretCursor.tenant_id == tenant))
    await session.execute(delete(Secret).where(Secret.tenant_id == tenant))
    for item in state.secrets:
        row = Secret(
            tenant_id=tenant,
            vault_id=item.vault_id,
            name=item.name,
            name_key=item.name.lower(),
            revision=max(v.sequence for v in item.versions),
            deleted_at=item.deleted_at,
            purge_at=item.purge_at,
        )
        session.add(row)
        await session.flush()
        for version in item.versions:
            session.add(
                SecretVersion(
                    tenant_id=tenant,
                    version=version.version,
                    secret_id=row.id,
                    encrypted_value=None,
                    **version.model_dump(exclude={"version"}),
                )
            )
    await session.flush()


async def prune_keyvault(session, tenant, now, limit):
    ids = list(
        await session.scalars(
            select(SecretCursor.id)
            .where(SecretCursor.tenant_id == tenant, SecretCursor.expires_at <= now)
            .order_by(SecretCursor.expires_at, SecretCursor.id)
            .limit(limit)
        )
    )
    if ids:
        await session.execute(
            delete(SecretCursor).where(SecretCursor.tenant_id == tenant, SecretCursor.id.in_(ids))
        )
    remaining = limit - len(ids)
    expired = list(
        await session.scalars(
            select(Secret.id)
            .where(
                Secret.tenant_id == tenant, Secret.deleted_at.is_not(None), Secret.purge_at <= now
            )
            .order_by(Secret.purge_at, Secret.id)
            .limit(remaining)
        )
    )
    if expired:
        await session.execute(
            delete(Secret).where(Secret.tenant_id == tenant, Secret.id.in_(expired))
        )
    remaining -= len(expired)
    deleted_rows = list(
        await session.scalars(
            select(DeletedVault)
            .where(DeletedVault.tenant_id == tenant, DeletedVault.purge_at <= now)
            .order_by(DeletedVault.purge_at, DeletedVault.vault_id)
            .limit(remaining)
        )
    )
    from mockazure.keyvault.vault_recovery import purge_vault

    for row in deleted_rows:
        await purge_vault(session, row)
    return len(ids) + len(expired) + len(deleted_rows)

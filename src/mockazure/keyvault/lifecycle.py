"""Default cloud fixtures contain secret metadata only, never value ciphertext."""

import re
from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import delete, select

from mockazure.admin.errors import AdminError
from mockazure.identity.types import IdentityInput
from mockazure.keyvault.contracts import checked_body, name_key
from mockazure.keyvault.models import Vault
from mockazure.keyvault.secret_models import Secret, SecretCursor, SecretVersion
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


class KeyVaultStateInput(IdentityInput):
    secrets: list[SecretInput] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def checked(self):
        if len({(s.vault_id, s.name.lower()) for s in self.secrets}) != len(self.secrets):
            raise ValueError("Duplicate secret name")
        versions = [v.version for s in self.secrets for v in s.versions]
        if len(versions) > 100000 or len(set(versions)) != len(versions):
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
                        version=v.id.hex,
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
    return KeyVaultStateInput(secrets=output)


async def replace_keyvault(session, tenant, state):
    for item in state.secrets:
        if await session.get(Vault, (tenant, item.vault_id)) is None:
            raise AdminError(422, "InvalidCloudState", "Secret vault is not owned by this tenant")
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
                    id=UUID(hex=version.version),
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
    return len(ids)

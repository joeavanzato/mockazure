"""Typed account fixtures; normal exports cannot carry credential material."""

import copy
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, model_validator
from sqlalchemy import delete, func, select, text, update

from mockazure.admin.errors import AdminError
from mockazure.cloud.events import lock_cloud_state
from mockazure.cloud.models import CloudEvent
from mockazure.content.models import StreamUpload
from mockazure.evidence.repository import lock_state
from mockazure.identity.types import IdentityInput
from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import resolve_scope
from mockazure.providers.arm.models import ArmResource, Subscription
from mockazure.providers.arm.scopes import parse_scope
from mockazure.security.repository import lock_security
from mockazure.storage.accounts import PROPERTIES, TYPE, account_name, checked_payload, new_key
from mockazure.storage.blob_lifecycle import (
    BlobInput,
    export_blobs,
    prune_blobs,
    restore_blobs,
    validate_blobs,
)
from mockazure.storage.endpoints import account_endpoints
from mockazure.storage.models import StorageAccount, StorageDelegationKey, StorageNamespace


class PolicyInput(IdentityInput):
    id: str = Field(min_length=1, max_length=64)
    start: str = ""
    expiry: str = ""
    permission: str = ""


class NamespaceInput(IdentityInput):
    service: Literal["blob", "file"]
    name: str = Field(min_length=3, max_length=63)
    etag: str = Field(min_length=3, max_length=64)
    created_at: datetime
    changed_at: datetime
    properties: dict = Field(default_factory=dict)
    policies: list[PolicyInput] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_namespace(self):
        from mockazure.providers.storage.errors import StorageError
        from mockazure.storage.containers import check_name
        from mockazure.storage.sas import timestamp

        try:
            check_name(self.name)
        except StorageError:
            raise ValueError("Invalid Storage namespace name") from None
        if (
            self.created_at.tzinfo is None
            or self.changed_at.tzinfo is None
            or not re.fullmatch(r'"[A-Za-z0-9]+"', self.etag)
        ):
            raise ValueError("Invalid namespace timestamp or ETag")
        allowed = {"metadata", "public_access"} if self.service == "blob" else {"metadata", "quota"}
        if set(self.properties) - allowed or self.properties.get("public_access", "") not in {
            "",
            "blob",
            "container",
        }:
            raise ValueError("Unsupported namespace property")
        if "quota" in self.properties and (
            type(self.properties["quota"]) is not int or not 1 <= self.properties["quota"] <= 102400
        ):
            raise ValueError("Invalid share quota")
        metadata = self.properties.get("metadata", {})
        if (
            not isinstance(metadata, dict)
            or any(
                not isinstance(k, str)
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
                or not isinstance(v, str)
                or not v.isascii()
                or any(ord(c) < 32 for c in v)
                for k, v in metadata.items()
            )
            or sum(len(k) + len(v) + 10 for k, v in metadata.items()) > 8192
        ):
            raise ValueError("Invalid metadata")
        if len({p.id for p in self.policies}) != len(self.policies):
            raise ValueError("Duplicate policy identifier")
        order = "racwdl" if self.service == "blob" else "rcwdl"
        for policy in self.policies:
            if "".join(c for c in order if c in policy.permission) != policy.permission:
                raise ValueError("Invalid policy permissions")
            for value in (policy.start, policy.expiry):
                if value:
                    timestamp(value)
            if (
                policy.start
                and policy.expiry
                and timestamp(policy.start) >= timestamp(policy.expiry)
            ):
                raise ValueError("Invalid policy interval")
        return self


class AccountInput(IdentityInput):
    id: str = Field(min_length=1, max_length=2048)
    configuration: dict
    services: dict = Field(default_factory=dict)
    blobs: list[BlobInput] = Field(default_factory=list, max_length=100000)
    namespaces: list[NamespaceInput] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def validate_account(self):
        scope = parse_scope(self.id)
        if len(scope.resource_parts) != 4 or tuple(
            p.lower() for p in scope.resource_parts[1:3]
        ) != ("microsoft.storage", "storageaccounts"):
            raise ValueError("Storage account ID required")
        account_name(scope.resource_parts[-1])
        self.configuration = checked_payload(self.configuration)
        if set(self.services) - {"blob", "file", "queue", "table"}:
            raise ValueError("Unknown Storage service")
        from mockazure.endpoints import EndpointProfile

        supported = account_endpoints(
            scope.resource_parts[-1], self.configuration["kind"], EndpointProfile().model_dump()
        )
        if not set(self.services) <= supported.keys():
            raise ValueError("Service does not belong to this account kind")
        if any(n.service not in supported for n in self.namespaces) or len(
            {(n.service, n.name) for n in self.namespaces}
        ) != len(self.namespaces):
            raise ValueError("Invalid or duplicate Storage namespace")
        containers = {n.name for n in self.namespaces if n.service == "blob"}
        if any(b.container not in containers for b in self.blobs) or len(
            {(b.container, b.name) for b in self.blobs}
        ) != len(self.blobs):
            raise ValueError("Invalid Blob namespace or duplicate name")
        for service, properties in self.services.items():
            validate_arm_payload(
                service.title() + "ServiceProperties",
                {"properties": properties},
                namespace="storage",
            )
            if (
                set(properties)
                - (
                    {"cors", "isVersioningEnabled", "deleteRetentionPolicy"}
                    if service == "blob"
                    else {"cors"}
                )
                or len(properties.get("cors", {}).get("corsRules", [])) > 5
            ):
                raise ValueError("Unsupported service properties")
            retention = properties.get("deleteRetentionPolicy", {})
            if set(retention) - {"enabled", "days"} or (
                retention.get("enabled") and not 1 <= retention.get("days", 0) <= 365
            ):
                raise ValueError("Invalid supported delete retention policy")
        return self


class StorageStateInput(IdentityInput):
    accounts: list[AccountInput] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def unique_accounts(self):
        keys = [parse_scope(row.id).key for row in self.accounts]
        names = [parse_scope(row.id).resource_parts[-1] for row in self.accounts]
        if len(keys) != len(set(keys)) or len(names) != len(set(names)):
            raise ValueError("Duplicate account ID or name")
        for values in (
            [b.id for a in self.accounts for b in a.blobs],
            [v.id for a in self.accounts for b in a.blobs for v in b.versions],
            [v.id for a in self.accounts for b in a.blobs for v in b.blocks],
        ):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate Blob state identity")
        return self


async def lock_accounts(session, tenant):
    await lock_security(session, tenant)
    await lock_state(session, tenant)
    await session.scalars(
        select(Subscription)
        .where(Subscription.tenant_id == tenant)
        .order_by(Subscription.id)
        .with_for_update()
    )
    await session.execute(text("SELECT pg_advisory_xact_lock(726385210058)"))
    await lock_cloud_state(session, tenant)


async def export_storage_accounts(session, tenant):
    await lock_accounts(session, tenant)
    rows = list(
        await session.execute(
            select(ArmResource, StorageAccount)
            .join(
                StorageAccount,
                (StorageAccount.tenant_id == ArmResource.tenant_id)
                & (StorageAccount.scope_key == ArmResource.scope_key),
            )
            .where(ArmResource.tenant_id == tenant)
            .order_by(ArmResource.scope_key)
            .limit(1001)
        )
    )
    if len(rows) > 1000:
        raise AdminError(413, "StorageStateLimit", "Account export exceeds the local limit")
    accounts = []
    for row, state in rows:
        accounts.append(
            AccountInput(
                id=row.display_id,
                configuration={
                    "location": row.location,
                    "tags": row.tags,
                    "kind": row.payload["kind"],
                    "sku": row.payload["sku"],
                    "properties": {
                        key: value
                        for key, value in row.payload["properties"].items()
                        if key in PROPERTIES
                    },
                },
                services=state.services,
                blobs=await export_blobs(session, tenant, state.scope_key),
                namespaces=[
                    NamespaceInput(
                        service=n.service,
                        name=n.name,
                        etag=n.etag,
                        created_at=n.created_at,
                        changed_at=n.changed_at,
                        properties=n.properties,
                        policies=n.policies,
                    )
                    for n in await session.scalars(
                        select(StorageNamespace)
                        .where(
                            StorageNamespace.tenant_id == tenant,
                            StorageNamespace.account_id == state.scope_key,
                        )
                        .order_by(StorageNamespace.service, StorageNamespace.name)
                        .limit(10001)
                    )
                ],
            )
        )
    return StorageStateInput(accounts=accounts).model_dump(mode="json")


async def replace_storage_accounts(session, tenant, value: StorageStateInput):
    await lock_accounts(session, tenant)
    current = list(
        await session.scalars(select(StorageAccount).where(StorageAccount.tenant_id == tenant))
    )
    old_keys = {row.scope_key for row in current}
    desired = {parse_scope(row.id).key for row in value.accounts}
    # Entire document validation precedes deletion, including ownership and global name conflicts.
    try:
        for item in value.accounts:
            await validate_blobs(session, tenant, item.blobs)
            scope = parse_scope(item.id)
            parent = await resolve_scope(session, tenant, parse_scope(scope.parent_keys[-1]))
            if parent.provisioning_state == "Deleting":
                raise ValueError("Parent is being deleted")
            collision = await session.scalar(
                select(StorageAccount).where(StorageAccount.name == scope.resource_parts[-1])
            )
            if collision and (collision.tenant_id != tenant or collision.scope_key not in old_keys):
                raise ValueError("Account name belongs to another scope")
            row = await session.get(ArmResource, (tenant, scope.key))
            if row is not None and row.resource_type.lower() != TYPE.lower():
                raise ValueError("Resource type mismatch")
    except (ArmError, ValueError):
        raise AdminError(
            422, "InvalidStorageState", "Invalid account ownership, parent or name"
        ) from None
    await session.execute(delete(StreamUpload).where(StreamUpload.tenant_id == tenant))
    if old_keys:
        await session.execute(
            update(CloudEvent)
            .where(CloudEvent.tenant_id == tenant, func.lower(CloudEvent.resource_id).in_(old_keys))
            .values(publish_eligible=False)
        )
        await session.execute(delete(StorageAccount).where(StorageAccount.tenant_id == tenant))
        removed = old_keys - desired
        if removed:
            await session.execute(
                delete(ArmResource).where(
                    ArmResource.tenant_id == tenant, ArmResource.scope_key.in_(removed)
                )
            )
    now = datetime.now(UTC)
    for item in value.accounts:
        scope = parse_scope(item.id)
        config = copy.deepcopy(item.configuration)
        row = await session.get(ArmResource, (tenant, scope.key))
        if row is None:
            row = ArmResource(
                tenant_id=tenant,
                subscription_id=scope.subscription_id,
                scope_key=scope.key,
                display_id=scope.display_id,
                kind="resource",
                group_key=scope.parent_keys[-1],
                name=scope.resource_parts[-1],
                resource_type=TYPE,
                created_at=now,
                changed_at=now,
                location=config["location"],
                tags={},
                payload={},
            )
            session.add(row)
        row.location, row.tags = config["location"], config["tags"]
        row.payload = {
            "kind": config["kind"],
            "sku": config["sku"],
            "properties": config["properties"]
            | {
                "creationTime": row.created_at.isoformat(),
                "provisioningState": "Succeeded",
                "primaryLocation": row.location,
                "statusOfPrimary": "available",
            },
        }
        row.changed_at = now
        row.revision = (row.revision or 0) + 1
        await session.flush()
        session.add(
            StorageAccount(
                tenant_id=tenant,
                scope_key=scope.key,
                name=row.name,
                key1=new_key(session),
                key2=new_key(session),
                key1_created=now,
                key2_created=now,
                services=copy.deepcopy(item.services),
            )
        )
    await session.flush()
    for item in value.accounts:
        scope = parse_scope(item.id)
        for namespace in item.namespaces:
            session.add(
                StorageNamespace(
                    tenant_id=tenant,
                    account_id=scope.key,
                    service=namespace.service,
                    name=namespace.name,
                    etag=namespace.etag,
                    created_at=namespace.created_at,
                    changed_at=namespace.changed_at,
                    properties=copy.deepcopy(namespace.properties),
                    policies=[p.model_dump() for p in namespace.policies],
                )
            )
    await session.flush()
    for item in value.accounts:
        await restore_blobs(session, tenant, parse_scope(item.id).key, item.blobs)
    for sub in await session.scalars(select(Subscription).where(Subscription.tenant_id == tenant)):
        sub.revision += 1
    await session.flush()
    return await export_storage_accounts(session, tenant)


async def reset_storage_accounts(session, tenant):
    return await replace_storage_accounts(session, tenant, StorageStateInput())


async def prune_storage_accounts(session, tenant, now, limit=500):
    if not 1 <= limit <= 1000 or now.tzinfo is None:
        raise ValueError("Invalid cleanup bound or time")
    await lock_accounts(session, tenant)
    ids = list(
        await session.scalars(
            select(StorageDelegationKey.id)
            .where(StorageDelegationKey.tenant_id == tenant, StorageDelegationKey.expires_at <= now)
            .order_by(StorageDelegationKey.expires_at, StorageDelegationKey.id)
            .limit(limit)
        )
    )
    if ids:
        await session.execute(
            delete(StorageDelegationKey).where(
                StorageDelegationKey.tenant_id == tenant, StorageDelegationKey.id.in_(ids)
            )
        )
    return len(ids) + (
        await prune_blobs(session, tenant, now, limit - len(ids)) if len(ids) < limit else 0
    )

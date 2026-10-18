"""Typed account fixtures; normal exports cannot carry credential material."""

import copy
from datetime import UTC, datetime

from pydantic import Field, model_validator
from sqlalchemy import delete, func, select, text, update

from mockazure.admin.errors import AdminError
from mockazure.cloud.events import lock_cloud_state
from mockazure.cloud.models import CloudEvent
from mockazure.evidence.repository import lock_state
from mockazure.identity.types import IdentityInput
from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import resolve_scope
from mockazure.providers.arm.models import ArmResource, Subscription
from mockazure.providers.arm.scopes import parse_scope
from mockazure.security.repository import lock_security
from mockazure.storage.accounts import PROPERTIES, TYPE, account_name, checked_payload, new_key
from mockazure.storage.endpoints import account_endpoints
from mockazure.storage.models import StorageAccount


class AccountInput(IdentityInput):
    id: str = Field(min_length=1, max_length=2048)
    configuration: dict
    services: dict = Field(default_factory=dict)

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
        for service, properties in self.services.items():
            validate_arm_payload(
                service.title() + "ServiceProperties",
                {"properties": properties},
                namespace="storage",
            )
            if (
                set(properties) - {"cors"}
                or len(properties.get("cors", {}).get("corsRules", [])) > 5
            ):
                raise ValueError("Unsupported service properties")
        return self


class StorageStateInput(IdentityInput):
    accounts: list[AccountInput] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def unique_accounts(self):
        keys = [parse_scope(row.id).key for row in self.accounts]
        names = [parse_scope(row.id).resource_parts[-1] for row in self.accounts]
        if len(keys) != len(set(keys)) or len(names) != len(set(names)):
            raise ValueError("Duplicate account ID or name")
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
    for sub in await session.scalars(select(Subscription).where(Subscription.tenant_id == tenant)):
        sub.revision += 1
    await session.flush()
    return await export_storage_accounts(session, tenant)


async def reset_storage_accounts(session, tenant):
    return await replace_storage_accounts(session, tenant, StorageStateInput())


async def prune_storage_accounts(session, tenant, now, limit=500):
    if not 1 <= limit <= 1000 or now.tzinfo is None:
        raise ValueError("Invalid cleanup bound or time")
    # Account/credential/service rows have no TTL; parent FK cascades remove them atomically.
    return 0

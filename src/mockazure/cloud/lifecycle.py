"""Explicit cloud lifecycle contributors; no runtime plugin discovery or credential exports."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError, model_validator
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.admin.errors import AdminError
from mockazure.calendar.lifecycle import (
    CalendarStateInput,
    export_calendars,
    prune_calendars,
    replace_calendars,
)
from mockazure.cloud.events import lock_cloud_state
from mockazure.cloud.models import CloudEvent
from mockazure.cloud.provider_state import (
    ProviderStateInput,
    export_provider_state,
    replace_provider_state,
)
from mockazure.db.job_model import Job
from mockazure.evidence.repository import lock_state
from mockazure.identity.types import IdentityInput
from mockazure.keyvault.lifecycle import (
    KeyVaultStateInput,
    export_keyvault,
    prune_keyvault,
    replace_keyvault,
)
from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.inventory import clean_public, seed_group
from mockazure.providers.arm.models import ArmResource, Subscription
from mockazure.providers.arm.resource_lifecycle import (
    ResourceStateInput,
    export_provider_resources,
    prune_provider_resources,
    replace_provider_resources,
)
from mockazure.providers.arm.scopes import parse_scope
from mockazure.security.repository import lock_security
from mockazure.storage.lifecycle import (
    StorageStateInput,
    export_storage_accounts,
    prune_storage_accounts,
    replace_storage_accounts,
)


class CloudSubscription(IdentityInput):
    id: UUID
    display_name: str = Field(min_length=1, max_length=200)
    state: Literal["Enabled", "Disabled", "Warned", "PastDue", "Deleted"] = "Enabled"
    tags: dict[str, str] = Field(default_factory=dict)


class CloudGroup(IdentityInput):
    id: str
    configuration: dict

    @model_validator(mode="after")
    def checked(self):
        scope = parse_scope(self.id)
        if scope.group is None or scope.resource_parts:
            raise ValueError("Group ID required")
        validate_arm_payload("ResourceGroup", self.configuration)
        clean_public(self.configuration)
        if set(self.configuration) - {"location", "tags", "managedBy"}:
            raise ValueError("Writable group fields required")
        return self


class CloudInput(IdentityInput):
    schema_version: Literal[1] = 1
    tenant_id: UUID
    generation: int = Field(ge=1, strict=True)
    subscriptions: list[CloudSubscription] = Field(default_factory=list, max_length=1000)
    groups: list[CloudGroup] = Field(default_factory=list, max_length=10000)
    provider: ProviderStateInput = Field(default_factory=ProviderStateInput)
    storage: StorageStateInput = Field(default_factory=StorageStateInput)
    resources: ResourceStateInput = Field(default_factory=ResourceStateInput)
    keyvault: KeyVaultStateInput = Field(default_factory=KeyVaultStateInput)
    calendars: CalendarStateInput = Field(default_factory=CalendarStateInput)

    @model_validator(mode="after")
    def distinct(self):
        if len({v.id for v in self.subscriptions}) != len(self.subscriptions) or len(
            {parse_scope(v.id).key for v in self.groups}
        ) != len(self.groups):
            raise ValueError("Duplicate cloud parent")
        return self


async def lock_lifecycle(session, tenant):
    # Acquire every domain lock before entering contributors; none may invert this order.
    await lock_security(session, tenant)
    await lock_state(session, tenant)
    await session.scalars(
        select(Subscription)
        .where(Subscription.tenant_id == tenant)
        .order_by(Subscription.id)
        .with_for_update()
    )
    await session.execute(text("SELECT pg_advisory_xact_lock(726385210058)"))
    await session.execute(text("SELECT pg_advisory_xact_lock(726385210059)"))
    return await lock_cloud_state(session, tenant)


async def retire_operations(session, tenant, *, include_groups=True):
    jobs = list(
        await session.scalars(
            select(Job.id)
            .where(
                Job.tenant_id == tenant,
                Job.kind.in_(
                    ("arm.provider.operation", "arm.group.delete")
                    if include_groups
                    else ("arm.provider.operation",)
                ),
                Job.state.in_(("pending", "running")),
            )
            .order_by(Job.id)
            .with_for_update()
        )
    )
    if jobs:
        await session.execute(
            update(ArmResource)
            .where(ArmResource.tenant_id == tenant, ArmResource.deletion_job_id.in_(jobs))
            .values(
                deletion_job_id=None,
                provisioning_state="Succeeded",
                revision=ArmResource.revision + 1,
            )
        )
        await session.execute(
            update(Job)
            .where(Job.id.in_(jobs))
            .values(
                state="failed",
                error_code="StaleCloudGeneration",
                lease_owner=None,
                lease_until=None,
                claim_epoch=Job.claim_epoch + 1,
                updated_at=datetime.now(UTC),
            )
        )
        for sub in await session.scalars(
            select(Subscription).where(Subscription.tenant_id == tenant)
        ):
            sub.revision += 1


async def invalidate_cloud(session, tenant, *, include_groups=True):
    state = await lock_lifecycle(session, tenant)
    state.generation += 1
    await retire_operations(session, tenant, include_groups=include_groups)
    await session.execute(
        update(CloudEvent).where(CloudEvent.tenant_id == tenant).values(publish_eligible=False)
    )
    await session.flush()


async def export_cloud(session: AsyncSession, tenant_id: UUID) -> dict:
    state = await lock_lifecycle(session, tenant_id)
    subscriptions = list(
        await session.scalars(
            select(Subscription)
            .where(Subscription.tenant_id == tenant_id)
            .order_by(Subscription.id)
            .limit(1001)
        )
    )
    groups = list(
        await session.scalars(
            select(ArmResource)
            .where(ArmResource.tenant_id == tenant_id, ArmResource.kind == "group")
            .order_by(ArmResource.scope_key)
            .limit(10001)
        )
    )
    if len(subscriptions) > 1000 or len(groups) > 10000:
        raise AdminError(413, "CloudStateLimit", "Cloud snapshot exceeds local limits")
    return CloudInput(
        tenant_id=tenant_id,
        generation=state.generation,
        subscriptions=[
            CloudSubscription.model_validate(v, from_attributes=True) for v in subscriptions
        ],
        groups=[
            CloudGroup(
                id=v.display_id, configuration={"location": v.location, "tags": v.tags, **v.payload}
            )
            for v in groups
        ],
        provider=await export_provider_state(session, tenant_id),
        storage=await export_storage_accounts(session, tenant_id),
        resources=await export_provider_resources(session, tenant_id),
        keyvault=await export_keyvault(session, tenant_id),
        calendars=await export_calendars(session, tenant_id),
    ).model_dump(mode="json")


async def import_cloud(session: AsyncSession, tenant_id: UUID, document: dict) -> None:
    try:
        value = CloudInput.model_validate(document)
    except (ValidationError, ValueError):
        raise AdminError(422, "InvalidCloudState", "Cloud snapshot has invalid fields") from None
    if value.tenant_id != tenant_id:
        raise AdminError(422, "InvalidCloudState", "Snapshot belongs to a different tenant")
    await lock_lifecycle(session, tenant_id)
    # A savepoint makes this interface atomic even when a caller handles validation errors.
    async with session.begin_nested():
        await retire_operations(session, tenant_id)
        for item in value.subscriptions:
            existing = await session.get(Subscription, item.id)
            if existing and existing.tenant_id != tenant_id:
                raise AdminError(
                    422, "InvalidCloudState", "Subscription belongs to a different tenant"
                )
            if existing is None:
                session.add(Subscription(tenant_id=tenant_id, **item.model_dump()))
            else:
                existing.display_name = item.display_name
                existing.state = item.state
                existing.tags = item.tags
                existing.revision += 1
        await session.flush()
        for group in value.groups:
            scope = parse_scope(group.id)
            existing = await session.get(Subscription, scope.subscription_id)
            if existing is None or existing.tenant_id != tenant_id:
                raise AdminError(422, "InvalidCloudState", "Group subscription is not owned")
            await seed_group(
                session, tenant_id, scope.subscription_id, scope.group, group.configuration
            )
        # Validation errors roll back operation retirement and all parent/config/key changes.
        await replace_provider_resources(session, tenant_id, value.resources)
        await replace_keyvault(session, tenant_id, value.keyvault)
        await replace_storage_accounts(session, tenant_id, value.storage)
        await replace_provider_state(session, tenant_id, value.provider)
        await replace_calendars(session, tenant_id, value.calendars)


async def reset_cloud(session: AsyncSession, tenant_id: UUID, expected_generation: int) -> None:
    state = await lock_lifecycle(session, tenant_id)
    if state.generation != expected_generation:
        raise AdminError(409, "CloudGenerationConflict", "Cloud generation changed")
    await retire_operations(session, tenant_id)
    await replace_keyvault(session, tenant_id, KeyVaultStateInput())
    await replace_provider_resources(session, tenant_id, ResourceStateInput())
    await replace_storage_accounts(session, tenant_id, StorageStateInput())
    await replace_provider_state(session, tenant_id, ProviderStateInput())
    await replace_calendars(session, tenant_id, CalendarStateInput())


async def prune_cloud(session: AsyncSession, tenant_id: UUID, now: datetime, limit: int) -> int:
    if not 1 <= limit <= 1000 or now.tzinfo is None:
        raise ValueError("Invalid cloud cleanup bounds")
    state = await lock_lifecycle(session, tenant_id)
    used = await prune_provider_resources(session, tenant_id, now, limit)
    if used < limit:
        used += await prune_storage_accounts(session, tenant_id, now, limit - used)
    if used < limit:
        used += await prune_keyvault(session, tenant_id, now, limit - used)
    if used < limit:
        used += await prune_calendars(session, tenant_id, now, limit - used)
    # Retired generations have no delivery authority. Active-generation facts are retained for F3.
    ids = (
        list(
            await session.scalars(
                select(CloudEvent.id)
                .where(
                    CloudEvent.tenant_id == tenant_id,
                    CloudEvent.generation != state.generation,
                    CloudEvent.publish_eligible.is_(False),
                )
                .order_by(CloudEvent.id)
                .limit(limit - used)
            )
        )
        if used < limit
        else []
    )
    if ids:
        await session.execute(
            delete(CloudEvent).where(CloudEvent.tenant_id == tenant_id, CloudEvent.id.in_(ids))
        )
    return used + len(ids)

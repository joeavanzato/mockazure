"""Idempotent service facts persisted in the caller's transaction."""

import json
from dataclasses import asdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.cloud.models import CloudEvent, CloudState
from mockazure.cloud.types import ServiceEvent
from mockazure.providers.arm.inventory import clean_public, resolve_scope
from mockazure.providers.arm.scopes import parse_scope


async def lock_cloud_state(session, tenant):
    await session.execute(
        insert(CloudState).values(tenant_id=tenant, generation=1).on_conflict_do_nothing()
    )
    return await session.scalar(
        select(CloudState)
        .where(CloudState.tenant_id == tenant)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def validate_event(event):
    if not isinstance(event.id, UUID) or not isinstance(event.tenant_id, UUID):
        raise ValueError("Event identities must be UUIDs")
    parse_scope(event.resource_id)
    if event.occurred_at.tzinfo is None or not isinstance(event.payload, dict):
        raise ValueError("Timestamp and object payload are required")
    for value, maximum in [(event.service, 64), (event.category, 128), (event.operation, 200)]:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("Invalid service event label")
    clean_public(event.payload)
    if len(json.dumps(event.payload, allow_nan=False).encode()) > 2 * 1024 * 1024:
        raise ValueError("Event payload exceeds the local limit")


async def append_service_event(session: AsyncSession, event: ServiceEvent) -> UUID:
    validate_event(event)
    scope = parse_scope(event.resource_id)
    # Deletion facts remain valid after the resource is gone; the owning
    # subscription must still belong to this tenant.
    await resolve_scope(
        session, event.tenant_id, parse_scope(f"/subscriptions/{scope.subscription_id}")
    )
    state = await lock_cloud_state(session, event.tenant_id)
    old = await session.get(CloudEvent, (event.tenant_id, event.id))
    values = asdict(event)
    if old:
        if any(getattr(old, key) != value for key, value in values.items()):
            raise ValueError("Event identity cannot be reused for different facts")
        return old.id
    session.add(CloudEvent(**values, generation=state.generation, publish_eligible=True))
    await session.flush()
    from mockazure.monitor.publication import queue_event

    await queue_event(session, event, state.generation)
    return event.id

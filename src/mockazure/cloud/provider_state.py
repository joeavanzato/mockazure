"""Typed provider/event lifecycle; restored facts never revive delivery authority."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator
from sqlalchemy import delete, select

from mockazure.admin.errors import AdminError
from mockazure.cloud.events import lock_cloud_state, validate_event
from mockazure.cloud.models import CloudEvent, ProviderRegistration
from mockazure.cloud.types import ServiceEvent
from mockazure.identity.types import IdentityInput
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import resolve_scope
from mockazure.providers.arm.models import Subscription
from mockazure.providers.arm.provider_registry import provider_name
from mockazure.providers.arm.scopes import parse_scope


class RegistrationInput(IdentityInput):
    subscription_id: UUID
    namespace: str = Field(min_length=1, max_length=128)
    state: Literal["Registered", "NotRegistered"]

    @field_validator("namespace")
    @classmethod
    def known_namespace(cls, value):
        try:
            return provider_name(value)
        except ArmError:
            raise ValueError("Unknown provider namespace") from None


class EventInput(IdentityInput):
    id: UUID
    resource_id: str = Field(min_length=1, max_length=2048)
    service: str = Field(min_length=1, max_length=64)
    category: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=200)
    occurred_at: AwareDatetime
    payload: dict


class ProviderStateInput(IdentityInput):
    registrations: list[RegistrationInput] = Field(default_factory=list, max_length=5000)
    events: list[EventInput] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def unique_records(self):
        keys = [(row.subscription_id, row.namespace) for row in self.registrations]
        if len(keys) != len(set(keys)) or len({row.id for row in self.events}) != len(self.events):
            raise ValueError("Duplicate provider state record")
        return self


async def lock_provider_state(session, tenant):
    # Match public registration: subscription before cloud publication state.
    await session.scalars(
        select(Subscription)
        .where(Subscription.tenant_id == tenant)
        .order_by(Subscription.id)
        .with_for_update()
    )
    return await lock_cloud_state(session, tenant)


async def export_provider_state(session, tenant):
    await lock_provider_state(session, tenant)
    registrations = list(
        await session.scalars(
            select(ProviderRegistration)
            .where(ProviderRegistration.tenant_id == tenant)
            .order_by(ProviderRegistration.subscription_id, ProviderRegistration.namespace_key)
            .limit(5001)
        )
    )
    events = list(
        await session.scalars(
            select(CloudEvent)
            .where(CloudEvent.tenant_id == tenant)
            .order_by(CloudEvent.id)
            .limit(10001)
        )
    )
    if len(registrations) > 5000 or len(events) > 10000:
        raise AdminError(413, "ProviderStateLimit", "Provider fixture export exceeds local limits")
    return {
        "registrations": [
            RegistrationInput(
                subscription_id=row.subscription_id,
                namespace=provider_name(row.namespace_key),
                state=row.state,
            ).model_dump(mode="json")
            for row in registrations
        ],
        "events": [
            EventInput.model_validate(row, from_attributes=True).model_dump(mode="json")
            for row in events
        ],
    }


async def replace_provider_state(session, tenant, value):
    state = await lock_provider_state(session, tenant)
    try:
        for row in value.registrations:
            await resolve_scope(
                session, tenant, parse_scope(f"/subscriptions/{row.subscription_id}")
            )
        for row in value.events:
            validate_event(ServiceEvent(tenant_id=tenant, **row.model_dump()))
            scope = parse_scope(row.resource_id)
            await resolve_scope(
                session, tenant, parse_scope(f"/subscriptions/{scope.subscription_id}")
            )
    except (ArmError, ValueError):
        raise AdminError(
            422,
            "InvalidProviderState",
            "All event scopes and registrations must be valid in this tenant",
        ) from None
    await session.execute(delete(CloudEvent).where(CloudEvent.tenant_id == tenant))
    await session.execute(
        delete(ProviderRegistration).where(ProviderRegistration.tenant_id == tenant)
    )
    state.generation += 1
    for row in value.registrations:
        session.add(
            ProviderRegistration(
                tenant_id=tenant,
                subscription_id=row.subscription_id,
                namespace_key=row.namespace.lower(),
                state=row.state,
            )
        )
    for row in value.events:
        session.add(
            CloudEvent(
                tenant_id=tenant,
                generation=state.generation,
                publish_eligible=False,
                **row.model_dump(),
            )
        )
    await session.flush()
    return await export_provider_state(session, tenant)


async def prune_provider_events(session, tenant, before, limit=500):
    if not 1 <= limit <= 1000:
        raise ValueError("Prune limit must be in1..1000")
    await lock_provider_state(session, tenant)
    ids = list(
        await session.scalars(
            select(CloudEvent.id)
            .where(
                CloudEvent.tenant_id == tenant,
                CloudEvent.publish_eligible.is_(False),
                CloudEvent.occurred_at < before,
            )
            .order_by(CloudEvent.occurred_at, CloudEvent.id)
            .limit(limit)
        )
    )
    if ids:
        await session.execute(
            delete(CloudEvent).where(CloudEvent.tenant_id == tenant, CloudEvent.id.in_(ids))
        )
    return len(ids)

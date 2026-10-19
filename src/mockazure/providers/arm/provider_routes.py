"""Subscription provider discovery and persisted synchronous registration."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Request
from sqlalchemy import select

from mockazure.cloud.events import append_service_event
from mockazure.cloud.models import ProviderRegistration
from mockazure.cloud.types import ServiceEvent
from mockazure.providers.arm.authorization import require_action
from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.facts import append_arm_fact
from mockazure.providers.arm.inventory import resolve_scope
from mockazure.providers.arm.inventory_routes import request_scope
from mockazure.providers.arm.models import ArmResource
from mockazure.providers.arm.mutations import transaction
from mockazure.providers.arm.probes import principal
from mockazure.providers.arm.provider_registry import (
    provider_name,
    provider_profiles,
    provider_view,
    resolve_operation,
)

router = APIRouter()


@asynccontextmanager
async def provider_transaction(request, scope, *, mutating):
    if mutating:
        # Existing ARM order: security, evidence, subscription, then cloud events.
        async with transaction(request, scope) as context:
            yield context
    else:
        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            sub = await resolve_scope(session, actor.tenant_id, scope)
            yield session, actor, sub


async def handle(request, subscription, namespace=None, action=None):
    args = request.query_params
    resolve_operation(request.method, request.url.path, args.get("api-version"))
    if set(args) != {"api-version"} or len(args.multi_items()) != 1:
        raise ArmError(400, "InvalidQueryParameterValue", "Unsupported provider query")
    scope = request_scope(subscription)
    name = provider_name(namespace) if namespace is not None else None
    if request.method == "POST":
        raw = await request.body()
        if raw.strip() not in {b"", b"{}"}:
            raise ArmError(
                400, "InvalidRequestContent", "Provider consent properties are unsupported"
            )
    async with provider_transaction(request, scope, mutating=action is not None) as (
        session,
        actor,
        sub,
    ):
        permission = (
            "Microsoft.Resources/subscriptions/providers/read"
            if action is None
            else name + "/" + action.lower() + "/action"
        )
        await require_action(session, actor, scope, permission)
        if action:
            if action.lower() not in {"register", "unregister"}:
                raise ArmError(
                    501, "MockAzureOperationNotImplemented", "Provider action is unsupported"
                )
            if action.lower() == "unregister":
                existing = await session.scalar(
                    select(ArmResource.scope_key)
                    .where(
                        ArmResource.tenant_id == actor.tenant_id,
                        ArmResource.subscription_id == sub.id,
                        ArmResource.resource_type.ilike(name + "/%"),
                    )
                    .limit(1)
                )
                if existing:
                    raise ArmError(409, "InvalidOperation", "Provider has existing resources")
            key = actor.tenant_id, sub.id, name.lower()
            row = await session.get(ProviderRegistration, key)
            before = provider_view(sub.id, name, row.state if row else "NotRegistered")
            state = "Registered" if action.lower() == "register" else "NotRegistered"
            if row is None:
                row = ProviderRegistration(
                    tenant_id=key[0], subscription_id=key[1], namespace_key=key[2], state=state
                )
                session.add(row)
            else:
                row.state = state
            after = provider_view(sub.id, name, state)
            if before != after:
                sub.revision += 1
                now, identifier = datetime.now(UTC), uuid4()
                await append_arm_fact(
                    session, actor, permission, scope, before, after, now, identifier
                )
                await append_service_event(
                    session,
                    ServiceEvent(
                        identifier,
                        actor.tenant_id,
                        scope.display_id,
                        "arm",
                        "Administrative",
                        permission,
                        now,
                        {"namespace": name, "registrationState": state},
                    ),
                )
            await session.flush()
            validate_arm_payload("Provider", after, namespace="resources")
            return after
        rows = {
            row.namespace_key: row.state
            for row in await session.scalars(
                select(ProviderRegistration).where(
                    ProviderRegistration.tenant_id == actor.tenant_id,
                    ProviderRegistration.subscription_id == sub.id,
                )
            )
        }
        if name:
            return provider_view(sub.id, name, rows.get(name.lower(), "NotRegistered"))
        return {
            "value": [
                provider_view(sub.id, name, rows.get(name.lower(), "NotRegistered"))
                for name in sorted(provider_profiles())
            ]
        }


@router.get("/subscriptions/{subscription}/providers")
async def providers_list(subscription: str, request: Request):
    return await handle(request, subscription)


@router.get("/subscriptions/{subscription}/providers/{namespace}")
async def providers_get(subscription: str, namespace: str, request: Request):
    return await handle(request, subscription, namespace)


@router.post("/subscriptions/{subscription}/providers/{namespace}/{action}")
async def providers_change(subscription: str, namespace: str, action: str, request: Request):
    return await handle(request, subscription, namespace, action)

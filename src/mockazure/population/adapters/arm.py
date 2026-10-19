"""Local Azure inventory provisioning; resource inventory does not deploy compute."""

from collections import defaultdict
from uuid import UUID

from pydantic import Field
from sqlalchemy import cast, func
from sqlalchemy.dialects.postgresql import JSONB

from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.types import (
    FamilyDescriptor,
    Input,
    OperationDescriptor,
    ResourceQuery,
    VerificationResult,
)
from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.inventory import (
    clean_public,
    seed_group,
    seed_resources,
    seed_subscription,
)
from mockazure.providers.arm.models import ArmResource, Subscription
from mockazure.providers.arm.scopes import parse_scope


def json_object(**fields):
    args = [item for pair in fields.items() for item in pair]
    return cast(func.jsonb_build_object(*args), JSONB)


class SubscriptionFixture(Input):
    id: UUID
    displayName: str = Field(min_length=1, max_length=200)
    state: str = "Enabled"


def descriptor(key, path, fields, schema, *, limitation):
    return FamilyDescriptor(
        key=f"arm.{key}",
        service="Azure management",
        api_path=path,
        display_fields=fields,
        operations=[
            OperationDescriptor(
                key="import",
                mode="fixture",
                method="POST",
                api_version="local-fixture-v1",
                path_template="/_mock/tenants/{tenant}/resources/arm." + key + "/apply",
                input_schema=schema,
            )
        ],
        contract_refs=["docs/arm-contracts.md"],
        limitations=[limitation],
    )


class SubscriptionAdapter(PayloadAdapter):
    async def verify(self, ctx, result):
        # The subscription revision fences the entire ARM inventory, including children.
        # Receipt readback verifies only the subscription fields this fixture owns.
        actual = 0
        for expected in result.items:
            page = await self.browse(ctx, ResourceQuery(identifier=expected.id))
            if len(page.items) == 1 and page.items[0].properties == expected.properties:
                actual += 1
        return VerificationResult(
            status="succeeded" if actual == len(result.items) else "failed",
            expected=len(result.items),
            actual=actual,
        )


def adapters():
    def normalize_subscription(payload):
        value = SubscriptionFixture.model_validate(payload)
        if value.state not in {"Enabled", "Disabled"}:
            raise ValueError("Unsupported subscription state")
        return value.model_dump(mode="json")

    async def write_subscription(session, ctx, pending):
        for _, payload in pending:
            await seed_subscription(session, ctx.tenant_id, payload)

    subscription = SubscriptionAdapter(
        descriptor(
            "subscriptions",
            "/subscriptions",
            ["id", "displayName", "state"],
            SubscriptionFixture.model_json_schema(),
            limitation="Subscriptions are provisioned locally; fixture import is create-only.",
        ),
        ReadSpec(
            Subscription,
            Subscription.id,
            json_object(
                id=Subscription.id, displayName=Subscription.display_name, state=Subscription.state
            ),
            Subscription.revision,
        ),
        normalize_subscription,
        write_subscription,
    )

    def normalize_group(payload):
        value = dict(payload)
        scope = parse_scope(value.get("id", ""))
        if scope.resource_parts or not scope.group:
            raise ValueError("Resource group ID required")
        value["id"] = scope.key
        validate_arm_payload("ResourceGroup", value)
        clean_public(value)
        return value

    async def write_group(session, ctx, pending):
        for _, payload in pending:
            scope = parse_scope(payload["id"])
            await seed_group(
                session,
                ctx.tenant_id,
                scope.subscription_id,
                scope.group,
                {k: v for k, v in payload.items() if k != "id"},
            )

    view = ArmResource.payload.op("||")(
        json_object(
            id=ArmResource.scope_key,
            name=ArmResource.name,
            type=ArmResource.resource_type,
            location=ArmResource.location,
            tags=ArmResource.tags,
        )
    )
    groups = PayloadAdapter(
        descriptor(
            "resourceGroups",
            "/subscriptions/{subscription}/resourcegroups",
            ["id", "name", "location", "tags"],
            {"type": "object", "required": ["id", "location"]},
            limitation="Local inventory provisioning with location and revision checks.",
        ),
        ReadSpec(
            ArmResource,
            ArmResource.scope_key,
            view,
            ArmResource.revision,
            (ArmResource.kind == "group",),
        ),
        normalize_group,
        write_group,
        replace_allowed=True,
    )

    def normalize_resource(payload):
        value = dict(payload)
        value["id"] = parse_scope(value.get("id", "")).key
        validate_arm_payload("GenericResourceExpanded", value)
        clean_public(value)
        return value

    async def write_resource(session, ctx, pending):
        grouped = defaultdict(list)
        for _, payload in pending:
            grouped[parse_scope(payload["id"]).subscription_id].append(payload)
        for subscription_id, values in grouped.items():
            await seed_resources(session, ctx.tenant_id, subscription_id, values)

    resources = PayloadAdapter(
        descriptor(
            "inventory",
            "/subscriptions/{subscription}/resources",
            ["id", "name", "type", "location"],
            {"type": "object", "required": ["id", "name", "type", "location"]},
            limitation="Create-only inventory; use provider API actions for operational resources.",
        ),
        ReadSpec(
            ArmResource,
            ArmResource.scope_key,
            view,
            ArmResource.revision,
            (ArmResource.kind == "resource",),
        ),
        normalize_resource,
        write_resource,
    )
    return [subscription, groups, resources]

"""Canonical ARM state operations shared by administration and public routes."""

from datetime import UTC, datetime

from sqlalchemy import select

from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.models import ArmResource, Subscription
from mockazure.providers.arm.scopes import parse_scope


async def resolve_scope(session, tenant, scope):
    subscription = await session.get(Subscription, scope.subscription_id)
    if subscription is None or subscription.tenant_id != tenant:
        raise ArmError(404, "SubscriptionNotFound", "Subscription was not found")
    if scope.group is None:
        return subscription
    row = await session.get(ArmResource, (tenant, scope.key))
    if row is None or row.subscription_id != scope.subscription_id:
        raise ArmError(404, "ResourceNotFound", "ARM scope was not found")
    return row


async def lock_subscription(session, tenant, identifier):
    row = await session.scalar(
        select(Subscription)
        .where(
            Subscription.tenant_id == tenant,
            Subscription.id == identifier,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ArmError(404, "SubscriptionNotFound", "Subscription was not found")
    return row


def clean_public(value, depth=0):
    from mockazure.mail.audit_schemas import PRIVATE

    if depth > 32:
        raise ValueError("Inventory nesting exceeds the local limit")
    if isinstance(value, dict):
        named = value.get("Name", value.get("name", value.get("Key", value.get("key"))))
        if isinstance(named, str) and named.replace("_", "").replace("-", "").lower() in PRIVATE:
            raise ValueError("Credential-bearing inventory is unsupported")
        for key, item in value.items():
            if key.replace("_", "").replace("-", "").lower() in PRIVATE:
                raise ValueError("Credential-bearing inventory is unsupported")
            clean_public(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            clean_public(item, depth + 1)


def checked_tags(tags):
    if not isinstance(tags, dict) or len(tags) > 50:
        raise ValueError("At most 50 tags are supported")
    names = set()
    for name, value in tags.items():
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 512
            or any(c in name for c in "<>%&\\?/")
            or any(ord(c) < 32 for c in name)
            or name.lower() in names
            or not isinstance(value, str)
            or len(value) > 256
        ):
            raise ValueError("Invalid tag name or value")
        names.add(name.lower())
    return dict(tags)


def inventory_view(row, expand=()):
    result = dict(row.payload)
    result.update(
        id=row.display_id,
        name=row.name,
        type=row.resource_type,
        location=row.location,
        tags=dict(row.tags),
    )
    if row.kind == "group":
        result["properties"] = {"provisioningState": row.provisioning_state}
    for field, value in (("createdTime", row.created_at), ("changedTime", row.changed_at)):
        if field in expand:
            result[field] = value.isoformat()
    if "provisioningState" in expand:
        result["provisioningState"] = row.provisioning_state
    return result


async def seed_group(session, tenant, subscription_id, name, value):
    subscription = await lock_subscription(session, tenant, subscription_id)
    scope = parse_scope(f"/subscriptions/{subscription_id}/resourceGroups/{name}")
    validate_arm_payload("ResourceGroup", value)
    clean_public(value)
    tags = checked_tags(value.get("tags", {}))
    row = await session.scalar(
        select(ArmResource)
        .where(
            ArmResource.tenant_id == tenant,
            ArmResource.scope_key == scope.key,
        )
        .execution_options(populate_existing=True)
    )
    if row and row.provisioning_state == "Deleting":
        raise ArmError(409, "ResourceGroupBeingDeleted", "Resource group deletion is in progress")
    if row and row.location.lower() != value["location"].lower():
        raise ArmError(409, "InvalidResourceGroupLocation", "Resource group location is immutable")
    payload = {k: value[k] for k in ("managedBy",) if k in value}
    if row is None:
        row = ArmResource(
            tenant_id=tenant,
            subscription_id=subscription_id,
            scope_key=scope.key,
            display_id=scope.display_id,
            kind="group",
            group_key=None,
            name=name,
            resource_type="Microsoft.Resources/resourceGroups",
            location=value["location"],
            tags=tags,
            payload=payload,
            provisioning_state="Succeeded",
            revision=1,
        )
        session.add(row)
        subscription.revision += 1
    elif row.tags != tags or row.payload != payload:
        row.tags, row.payload = tags, payload
        row.revision += 1
        row.changed_at = datetime.now(UTC)
        subscription.revision += 1
    await session.flush()
    return row


async def seed_resources(session, tenant, subscription_id, values):
    subscription = await lock_subscription(session, tenant, subscription_id)
    if not isinstance(values, list) or not 1 <= len(values) <= 1000:
        raise ValueError("Import requires 1..1000 inventory resources")
    seen, result = set(), []
    for value in values:
        validate_arm_payload("GenericResourceExpanded", value)
        clean_public(value)
        scope = parse_scope(value.get("id", ""))
        if scope.subscription_id != subscription_id or not scope.resource_parts:
            raise ValueError("Inventory resource requires the owning subscription and group")
        if scope.key in seen:
            raise ValueError("Duplicate resource identity in import")
        seen.add(scope.key)
        group_scope = parse_scope(f"/subscriptions/{subscription_id}/resourceGroups/{scope.group}")
        group = await resolve_scope(session, tenant, group_scope)
        if group.provisioning_state == "Deleting":
            raise ArmError(
                409, "ResourceGroupBeingDeleted", "Resource group deletion is in progress"
            )
        parts = scope.resource_parts
        name, resource_type = "/".join(parts[3::2]), parts[1] + "/" + "/".join(parts[2::2])
        if (
            value.get("name", "").lower() != name.lower()
            or value.get("type", "").lower() != resource_type.lower()
        ):
            raise ValueError("Resource name/type disagrees with canonical ID")
        if not isinstance(value.get("location"), str) or not 1 <= len(value["location"]) <= 100:
            raise ValueError("Resource location is required")
        if len(value["name"]) > 1024 or len(value["type"]) > 512:
            raise ValueError("Resource name or type exceeds the local storage limit")
        state = value.get("provisioningState", "Succeeded")
        if not isinstance(state, str) or not 1 <= len(state) <= 32:
            raise ValueError("Invalid provisioning state")
        tags = checked_tags(value.get("tags", {}))
        payload = {
            k: v
            for k, v in value.items()
            if k
            not in {
                "id",
                "name",
                "type",
                "location",
                "tags",
                "createdTime",
                "changedTime",
                "provisioningState",
            }
        }
        row = await session.scalar(
            select(ArmResource)
            .where(
                ArmResource.tenant_id == tenant,
                ArmResource.scope_key == scope.key,
            )
            .execution_options(populate_existing=True)
        )
        changed = row is None or any(
            (
                row.tags != tags,
                row.payload != payload,
                row.location != value["location"],
                row.provisioning_state != state,
            )
        )
        now = datetime.now(UTC)
        created = (
            datetime.fromisoformat(value["createdTime"].replace("Z", "+00:00"))
            if "createdTime" in value
            else row.created_at
            if row
            else now
        )
        modified = (
            datetime.fromisoformat(value["changedTime"].replace("Z", "+00:00"))
            if "changedTime" in value
            else now
            if changed
            else row.changed_at
        )
        if modified < created:
            raise ValueError("Resource changedTime precedes createdTime")
        if row is None:
            row = ArmResource(
                tenant_id=tenant,
                subscription_id=subscription_id,
                scope_key=scope.key,
                display_id=scope.display_id,
                kind="resource",
                group_key=group_scope.key,
                name=value["name"],
                resource_type=value["type"],
                location=value["location"],
                tags=tags,
                payload=payload,
                provisioning_state=state,
                created_at=created,
                changed_at=modified,
                revision=1,
            )
            session.add(row)
            subscription.revision += 1
        elif changed or row.created_at != created or row.changed_at != modified:
            row.tags, row.payload, row.location = tags, payload, value["location"]
            row.provisioning_state = state
            row.created_at, row.changed_at = created, modified
            row.revision += 1
            subscription.revision += 1
        await session.flush()
        result.append(inventory_view(row))
    return result

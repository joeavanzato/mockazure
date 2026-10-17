"""Serialized ARM mutations; state and evidence commit or roll back together."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

from mockazure.evidence.repository import lock_state
from mockazure.providers.arm.authorization import require_action
from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.facts import append_arm_fact
from mockazure.providers.arm.inventory import (
    checked_tags,
    clean_public,
    inventory_view,
    lock_subscription,
    resolve_scope,
    seed_group,
)
from mockazure.providers.arm.models import ArmResource, Subscription
from mockazure.providers.arm.probes import principal
from mockazure.providers.arm.scopes import parse_scope
from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError
from mockazure.security.repository import lock_security


async def body(request):
    try:
        return await read_json(request, 2 * 1024 * 1024)
    except GraphError as error:
        raise ArmError(error.status, "InvalidRequestContent", error.message) from None


@asynccontextmanager
async def transaction(request, scope):
    try:
        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            await lock_security(session, actor.tenant_id)
            await lock_state(session, actor.tenant_id)
            sub = await lock_subscription(session, actor.tenant_id, scope.subscription_id)
            yield session, actor, sub
    except ValueError:
        raise ArmError(400, "InvalidRequestContent", "Unsupported or invalid ARM payload") from None


async def group_write(request, scope, value, *, partial):
    async with transaction(request, scope) as (session, actor, sub):
        existing = await session.get(ArmResource, (actor.tenant_id, scope.key))
        authority = scope if existing else parse_scope(f"/subscriptions/{scope.subscription_id}")
        await require_action(
            session, actor, authority, "Microsoft.Resources/subscriptions/resourceGroups/write"
        )
        if partial and not existing:
            raise ArmError(404, "ResourceGroupNotFound", "Resource group was not found")
        validate_arm_payload("ResourceGroupPatchable" if partial else "ResourceGroup", value)
        if partial:
            # Original Az.Resources serializes these published Patchable fields.
            # A group cannot be renamed or have its read-only state overwritten.
            if "name" in value and value["name"].lower() != scope.group.lower():
                raise ValueError("Resource group name cannot change")
            if value.get("properties", {}) != {}:
                raise ValueError("Resource group properties are read-only")
            value = {key: item for key, item in value.items() if key not in {"name", "properties"}}
        if set(value) - ({"tags", "managedBy"} if partial else {"location", "tags", "managedBy"}):
            raise ValueError("Unsupported writable resource group property")
        if not partial and (
            not isinstance(value.get("location"), str) or not 1 <= len(value["location"]) <= 100
        ):
            raise ValueError("Resource group location is required")
        before = inventory_view(existing) if existing else {}
        if partial:
            merged = {
                "location": existing.location,
                "tags": dict(existing.tags),
                **existing.payload,
            }
            merged.update(value)
        else:
            merged = value
        row = await seed_group(session, actor.tenant_id, scope.subscription_id, scope.group, merged)
        after = inventory_view(row)
        await append_arm_fact(
            session,
            actor,
            "Microsoft.Resources/subscriptions/resourceGroups/write",
            scope,
            before,
            after,
            datetime.now(UTC),
            uuid4(),
        )
        return after, 200 if existing else 201


def tag_view(scope, tags):
    return {
        "id": scope.display_id + "/providers/Microsoft.Resources/tags/default",
        "name": "default",
        "type": "Microsoft.Resources/tags",
        "properties": {"tags": dict(tags)},
    }


def apply_tags(current, value, method):
    if method == "DELETE":
        return {}
    clean_public(value)
    validate_arm_payload("TagsPatchResource" if method == "PATCH" else "TagsResource", value)
    if set(value) - ({"operation", "properties"} if method == "PATCH" else {"properties"}):
        raise ValueError("Unsupported writable tag property")
    if set(value.get("properties", {})) != {"tags"}:
        raise ValueError("tags are required")
    incoming = checked_tags(value["properties"]["tags"])
    operation = value.get("operation") if method == "PATCH" else "Replace"
    if operation not in {"Merge", "Replace", "Delete"}:
        raise ValueError("Unsupported tag operation")
    if operation == "Replace":
        return incoming
    result = dict(current)
    names = {key.lower(): key for key in result}
    for key, val in incoming.items():
        canonical = names.get(key.lower(), key)
        if operation == "Merge":
            result[canonical] = val
        elif canonical in result and (val == "" or result[canonical] == val):
            del result[canonical]
    return checked_tags(result)


async def tags_write(request, scope, value):
    async with transaction(request, scope) as (session, actor, sub):
        action = "Microsoft.Resources/tags/" + ("delete" if request.method == "DELETE" else "write")
        await require_action(session, actor, scope, action)
        row = await resolve_scope(session, actor.tenant_id, scope)
        group_key = (
            row.group_key if isinstance(row, ArmResource) and row.kind == "resource" else None
        )
        group = await session.get(ArmResource, (actor.tenant_id, group_key)) if group_key else row
        if isinstance(group, ArmResource) and group.provisioning_state == "Deleting":
            raise ArmError(
                409, "ResourceGroupBeingDeleted", "Resource group deletion is in progress"
            )
        before = tag_view(scope, row.tags)
        tags = apply_tags(row.tags, value, request.method)
        if row.tags != tags:
            row.tags = tags
            row.revision += 1
            if not isinstance(row, Subscription):
                row.changed_at = datetime.now(UTC)
                sub.revision += 1
        after = tag_view(scope, row.tags)
        await append_arm_fact(
            session, actor, action, scope, before, after, datetime.now(UTC), uuid4()
        )
        return after

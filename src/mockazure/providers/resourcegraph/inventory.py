"""SQL authorization filters precede all query-engine tables and joins."""

import json

from sqlalchemy import String, cast, literal, select

from mockazure.providers.arm.authorization import grant_query, readable_scopes
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import inventory_view
from mockazure.providers.arm.models import ArmResource, RoleAssignment, Subscription
from mockazure.query.types import QueryError, QueryResult

COLUMNS = [
    ("id", "string"),
    ("name", "string"),
    ("type", "string"),
    ("tenantId", "string"),
    ("kind", "string"),
    ("location", "string"),
    ("resourceGroup", "string"),
    ("subscriptionId", "string"),
    ("managedBy", "string"),
    ("sku", "dynamic"),
    ("plan", "dynamic"),
    ("properties", "dynamic"),
    ("tags", "dynamic"),
    ("zones", "dynamic"),
    ("extendedLocation", "dynamic"),
    ("identity", "dynamic"),
]


async def authorized_tables(session, actor, scope):
    granted = await grant_query(session, actor, "Microsoft.Resources/subscriptions/resources/read")
    allowed = select(Subscription.id).where(
        Subscription.tenant_id == actor.tenant_id,
        Subscription.id.in_(granted.with_only_columns(RoleAssignment.subscription_id)),
    )
    if scope.subscription_ids:
        allowed = allowed.where(Subscription.id.in_(scope.subscription_ids))
    ids = list(await session.scalars(allowed.limit(10001)))
    if not ids:
        raise ArmError(
            403, "AuthorizationFailed", "No requested subscriptions have current readable resources"
        )
    if len(ids) > 10000:
        raise QueryError("Subscription scope limit exceeded", "QueryLimitExceeded")
    tables = {"Resources": [], "ResourceContainers": []}
    size = 0
    count = 0

    def append(table, value):
        nonlocal size, count
        row = [value.get(name, "" if kind == "string" else None) for name, kind in COLUMNS]
        size += len(json.dumps(row, separators=(",", ":")).encode()) + 64 + 32 * len(row)
        count += 1
        if size > 64 * 1024 * 1024 or count > 1000000:
            raise QueryError("Authorized query inputs exceed emulator limits", "QueryLimitExceeded")
        tables[table].append(row)

    predicate = await readable_scopes(
        session, actor, "Microsoft.Resources/subscriptions/resources/read"
    )
    query = (
        select(ArmResource)
        .where(
            ArmResource.tenant_id == actor.tenant_id,
            ArmResource.subscription_id.in_(ids),
            predicate,
        )
        .order_by(ArmResource.scope_key)
    )
    async for row in await session.stream_scalars(query.execution_options(yield_per=500)):
        value = inventory_view(row)
        value.update(
            id=row.display_id,
            name=row.name,
            type=row.resource_type.lower(),
            tenantId=str(actor.tenant_id),
            subscriptionId=str(row.subscription_id),
            resourceGroup=row.group_key.rsplit("/", 1)[-1] if row.group_key else "",
        )
        append("Resources" if row.kind == "resource" else "ResourceContainers", value)
    subscription_grants = await grant_query(
        session, actor, "Microsoft.Resources/subscriptions/read"
    )
    permitted = (
        subscription_grants.where(
            RoleAssignment.subscription_id == Subscription.id,
            RoleAssignment.scope_key == literal("/subscriptions/") + cast(Subscription.id, String),
        )
        .correlate(Subscription)
        .exists()
    )
    query = (
        select(Subscription)
        .where(Subscription.tenant_id == actor.tenant_id, Subscription.id.in_(ids), permitted)
        .order_by(Subscription.id)
    )
    async for row in await session.stream_scalars(query.execution_options(yield_per=500)):
        append(
            "ResourceContainers",
            dict(
                id="/subscriptions/" + str(row.id),
                name=row.display_name,
                type="microsoft.resources/subscriptions",
                tenantId=str(actor.tenant_id),
                subscriptionId=str(row.id),
                properties={"state": row.state},
                tags=row.tags,
            ),
        )
    return {name: QueryResult(list(COLUMNS), rows, False) for name, rows in tables.items()}

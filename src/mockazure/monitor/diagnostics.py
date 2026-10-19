"""Schema-bound diagnostic settings with current source/destination permissions."""

import copy
import re
from uuid import uuid4

from sqlalchemy import func, select

from mockazure.cloud.preconditions import check_local_write
from mockazure.cloud.types import MutationResult
from mockazure.monitor.diagnostic_models import DiagnosticSetting
from mockazure.monitor.workspaces import Workspace
from mockazure.providers.arm.authorization import grant_query, require_action
from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import resolve_scope
from mockazure.providers.arm.models import ArmResource, RoleAssignment
from mockazure.providers.arm.scopes import parse_scope
from mockazure.storage.models import StorageAccount

STORAGE = {
    "blobservices": "blob",
    "fileservices": "file",
    "queueservices": "queue",
    "tableservices": "table",
}
ACTIVITY = (
    "Administrative",
    "Security",
    "ServiceHealth",
    "Alert",
    "Recommendation",
    "Policy",
    "Autoscale",
    "ResourceHealth",
)


async def source_scope(session, tenant, resource_id):
    scope = parse_scope(resource_id)
    if scope.group is None:
        await resolve_scope(session, tenant, scope)
        return scope, "arm", ACTIVITY
    parts = scope.key.split("/")
    if len(parts) >= 3 and parts[-2] in STORAGE and parts[-1] == "default":
        parent = parse_scope("/".join(resource_id.split("/")[:-2]))
        account = await session.get(StorageAccount, (tenant, parent.key))
        if account is None:
            raise ArmError(404, "ResourceNotFound", "Storage account was not found")
        return scope, STORAGE[parts[-2]], ("StorageRead", "StorageWrite", "StorageDelete")
    row = await resolve_scope(session, tenant, scope)
    if row.resource_type.lower() == "microsoft.keyvault/vaults":
        return scope, "keyvault", ("AuditEvent",)
    raise ArmError(
        400, "DiagnosticSettingNotSupported", "This resource has no modeled log categories"
    )


async def source_permission(session, actor, scope, action):
    query = await grant_query(session, actor, "Microsoft.Insights/" + action)
    allowed = await session.scalar(
        query.where(RoleAssignment.scope_key.in_((*scope.parent_keys, scope.key))).limit(1)
    )
    if allowed is None:
        raise ArmError(
            403, "AuthorizationFailed", "Current source diagnostic permission is required"
        )


def view(row):
    return {
        "id": row.resource_id + "/providers/Microsoft.Insights/diagnosticSettings/" + row.name,
        "name": row.name,
        "type": "Microsoft.Insights/diagnosticSettings",
        "properties": copy.deepcopy(row.properties),
    }


async def categories(session, actor, resource_id, name=None):
    scope, service, names = await source_scope(session, actor.tenant_id, resource_id)
    await source_permission(session, actor, scope, "diagnosticSettingsCategories/read")
    values = [
        {
            "id": scope.display_id
            + "/providers/Microsoft.Insights/diagnosticSettingsCategories/"
            + n,
            "name": n,
            "type": "Microsoft.Insights/diagnosticSettingsCategories",
            "properties": {
                "categoryType": "Logs",
                "categoryGroups": ["allLogs"] + (["audit"] if service == "keyvault" else []),
            },
        }
        for n in names
    ]
    if name is not None:
        value = next((v for v in values if v["name"].lower() == name.lower()), None)
        if value is None:
            raise ArmError(404, "ResourceNotFound", "Log category was not found")
        return value
    return {"value": values}


async def destination_caps(session, actor, properties, *, administrative=False):
    values = []
    for field, kind, resource_type, actions in (
        (
            "workspaceId",
            "workspace",
            "microsoft.operationalinsights/workspaces",
            (
                "Microsoft.OperationalInsights/workspaces/read",
                "Microsoft.OperationalInsights/workspaces/sharedKeys/action",
            ),
        ),
        (
            "storageAccountId",
            "storage",
            "microsoft.storage/storageaccounts",
            (
                "Microsoft.Storage/storageAccounts/read",
                "Microsoft.Storage/storageAccounts/listKeys/action",
            ),
        ),
    ):
        if not properties.get(field):
            continue
        scope = parse_scope(properties[field])
        row = await resolve_scope(session, actor.tenant_id, scope)
        if not isinstance(row, ArmResource) or row.resource_type.lower() != resource_type:
            raise ArmError(
                400, "InvalidDestination", "Destination has an unsupported resource type"
            )
        if not administrative:
            for action in actions:
                await require_action(session, actor, scope, action)
        value = {"kind": kind, "resource": scope.key, "identity": row.created_at.isoformat()}
        if kind == "workspace":
            workspace = await session.get(Workspace, (actor.tenant_id, scope.key))
            if workspace is None:
                raise ArmError(400, "InvalidDestination", "Workspace destination is not active")
            value["workspace"] = str(workspace.customer_id)
            value["mode"] = properties.get("logAnalyticsDestinationType") or "AzureDiagnostics"
        else:
            account = await session.get(StorageAccount, (actor.tenant_id, scope.key))
            if account is None or row.payload.get("kind") == "FileStorage":
                raise ArmError(400, "InvalidDestination", "Destination must support Blob storage")
        values.append(value)
    if not values:
        raise ArmError(
            400, "InvalidDestination", "At least one supported local destination is required"
        )
    return values


def validated_properties(body, service, names):
    namespace = "subscriptionDiagnostics" if service == "arm" else "diagnostics"
    schema = (
        "SubscriptionDiagnosticSettingsResource"
        if service == "arm"
        else "DiagnosticSettingsResource"
    )
    try:
        validate_arm_payload(schema, body, namespace=namespace)
        if set(body) != {"properties"}:
            raise ValueError()
        properties = copy.deepcopy(body["properties"])
        if set(properties) - {
            "workspaceId",
            "storageAccountId",
            "logAnalyticsDestinationType",
            "logs",
            "metrics",
        } or properties.get("metrics"):
            raise ValueError()
        if properties.get("logAnalyticsDestinationType") not in {None, "Dedicated"}:
            raise ValueError()
        logs = properties.get("logs", [])
        if not logs or len(logs) > 16:
            raise ValueError()
        selected = set()
        for item in logs:
            if set(item) - {"category", "categoryGroup", "enabled", "retentionPolicy"} or (
                "category" in item
            ) == ("categoryGroup" in item):
                raise ValueError()
            retention = item.get("retentionPolicy", {})
            if retention.get("enabled") or retention.get("days", 0) != 0:
                raise ValueError()
            if "category" in item:
                if item["category"] not in names:
                    raise ValueError()
                choices = {item["category"]}
            else:
                if item["categoryGroup"] not in (
                    {"allLogs", "audit"} if service == "keyvault" else {"allLogs"}
                ):
                    raise ValueError()
                choices = set(names)
            if selected & choices:
                raise ValueError()
            selected |= choices
        return properties
    except (ValueError, TypeError, KeyError):
        raise ArmError(
            400, "BadRequest", "Unsupported diagnostic category, destination or property"
        ) from None


async def mutate_diagnostic_setting(session, actor, resource_id, name, operation, body, now):
    scope, service, names = await source_scope(session, actor.tenant_id, resource_id)
    await source_permission(
        session,
        actor,
        scope,
        "diagnosticSettings/"
        + (
            "read"
            if operation in {"get", "list"}
            else "delete"
            if operation == "delete"
            else "write"
        ),
    )
    query = select(DiagnosticSetting).where(
        DiagnosticSetting.tenant_id == actor.tenant_id,
        DiagnosticSetting.resource_key == scope.key,
        DiagnosticSetting.deleted.is_(False),
    )
    if operation == "list":
        return MutationResult(
            200,
            {
                "value": [
                    view(r)
                    for r in await session.scalars(query.order_by(DiagnosticSetting.name_key))
                ]
            },
            {},
        )
    if not isinstance(name, str) or not re.fullmatch(r"[^/\\?#%\x00-\x1f]{1,260}", name):
        raise ArmError(400, "InvalidName", "Invalid diagnostic setting name")
    row = await session.scalar(query.where(DiagnosticSetting.name_key == name.lower()))
    before = view(row) if row else {}
    if operation == "get":
        if row is None:
            raise ArmError(404, "ResourceNotFound", "Diagnostic setting was not found")
    elif operation == "delete":
        if row:
            row.deleted = True
            row.updated_at = now
            await publish_setting(session, actor, scope, name, before, {}, now, "delete")
        return MutationResult(204, None, {})
    elif operation == "put":
        check_local_write(session.info.get("local_write_headers", {}), row, ArmError)
        properties = validated_properties(body, service, names)
        destinations = await destination_caps(session, actor, properties)
        if row is None:
            count = await session.scalar(
                select(func.count())
                .select_from(DiagnosticSetting)
                .where(
                    DiagnosticSetting.tenant_id == actor.tenant_id,
                    DiagnosticSetting.resource_key == scope.key,
                    DiagnosticSetting.deleted.is_(False),
                )
            )
            if count >= 5:
                raise ArmError(
                    400,
                    "DiagnosticSettingsQuotaExceeded",
                    "At most five diagnostic settings per resource",
                )
            row = DiagnosticSetting(
                tenant_id=actor.tenant_id,
                resource_key=scope.key,
                resource_id=scope.display_id,
                name=name,
                name_key=name.lower(),
            )
            session.add(row)
        # An attachment keeps its capability only while continuously configured.
        # Removing and re-adding the same resource must never revive captured work.
        for destination in destinations:
            previous = next(
                (
                    d
                    for d in (row.destinations or [])
                    if all(d.get(k) == destination[k] for k in ("kind", "resource", "identity"))
                ),
                None,
            )
            destination["epoch"] = previous.get("epoch") if previous else str(uuid4())
        row.properties, row.destinations, row.updated_at = properties, destinations, now
        await session.flush()
        await publish_setting(session, actor, scope, name, before, view(row), now, "write")
    else:
        raise ArmError(405, "MethodNotAllowed", "Unsupported diagnostic operation")
    return MutationResult(200, view(row), {})


async def publish_setting(session, actor, scope, name, before, after, now, operation):
    from uuid import uuid4

    from mockazure.providers.arm.facts import append_arm_fact

    # ARM evidence accepts ordinary resource scopes; the diagnostic extension ID
    # remains in the before/after public state and operation label.
    await append_arm_fact(
        session,
        actor,
        "Microsoft.Insights/diagnosticSettings/" + operation,
        scope,
        before,
        after,
        now,
        uuid4(),
    )

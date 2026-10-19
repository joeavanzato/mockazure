"""Typed provider operations over one canonical ARM resource inventory."""

import copy
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Request, Response
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from mockazure.cloud.events import append_service_event
from mockazure.cloud.models import ProviderRegistration
from mockazure.cloud.types import MutationResult, ResourceRef, ServiceEvent
from mockazure.identity.types import PrincipalContext
from mockazure.keyvault.models import Vault
from mockazure.monitor.workspaces import Workspace
from mockazure.providers.arm.authorization import grant_query, readable_scopes, require_action
from mockazure.providers.arm.contracts import document, manifest, object_shape, validate_arm_payload
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.facts import append_arm_fact
from mockazure.providers.arm.inventory import resolve_scope
from mockazure.providers.arm.inventory_routes import request_scope
from mockazure.providers.arm.models import (
    ArmResource,
    ProviderResource,
    ResourceRelationship,
    RoleAssignment,
    Subscription,
)
from mockazure.providers.arm.mutations import body, transaction
from mockazure.providers.arm.paging import next_link, read_page
from mockazure.providers.arm.provider_registry import resolve_operation
from mockazure.providers.arm.provider_routes import provider_transaction
from mockazure.providers.arm.resource_relationships import (
    replace_references,
    require_linked_actions,
    validate_references,
)
from mockazure.providers.arm.resource_validation import (
    configuration,
    merge,
    profile_for,
    validate_resource,
    validate_wire,
)
from mockazure.providers.arm.scopes import parse_scope

router = APIRouter()


async def resource_view(session, row, profile, *, endpoints=None):
    result = copy.deepcopy(row.payload)
    result.setdefault("properties", {})["provisioningState"] = row.provisioning_state
    result.update(id=row.display_id, name=row.name, type=row.resource_type)
    if profile["locationRequired"]:
        result.update(location=row.location, tags=copy.deepcopy(row.tags))
    source = manifest()["roots"][profile["schemaNamespace"]]
    fields = object_shape(document(source)["definitions"][profile["responseType"]], source)[0]
    if "etag" in fields:
        result["etag"] = 'W/"' + str(row.revision) + '"'
    for child in profile["children"]:
        child_type, child_profile = profile_for(row.resource_type + "/" + child)
        children = await session.scalars(
            select(ArmResource)
            .join(
                ProviderResource,
                (ProviderResource.tenant_id == ArmResource.tenant_id)
                & (ProviderResource.scope_key == ArmResource.scope_key),
            )
            .where(
                ArmResource.tenant_id == row.tenant_id,
                ArmResource.resource_type == child_type,
                ArmResource.scope_key.startswith(
                    row.scope_key + "/" + child.lower() + "/", autoescape=True
                ),
            )
            .order_by(ArmResource.scope_key)
        )
        values = [
            await resource_view(session, item, child_profile, endpoints=endpoints)
            for item in children
        ]
        if profile["family"] == "vm":
            # VM extensions use the official top-level resources field.
            result["resources"] = values
        else:
            result["properties"][child] = values
    if profile["family"] == "vault" and endpoints is not None:
        result["properties"]["vaultUri"] = endpoints.keyvault + "/" + row.name.lower() + "/"
    validate_arm_payload(profile["responseType"], result, namespace=profile["schemaNamespace"])
    return result


async def publish(session, actor, scope, action, before, after, now):
    if before == after:
        return
    sub = await session.get(Subscription, scope.subscription_id)
    sub.revision += 1
    event_id = uuid4()
    await append_arm_fact(session, actor, action, scope, before, after, now, event_id)
    await append_service_event(
        session,
        ServiceEvent(
            event_id,
            actor.tenant_id,
            scope.display_id,
            "arm",
            "Administrative",
            action,
            now,
            {"resourceType": after.get("type", before.get("type"))},
        ),
    )


async def mutate_resource(
    session: AsyncSession,
    actor: PrincipalContext,
    ref: ResourceRef,
    operation: str,
    body: dict,
    now: datetime,
) -> MutationResult:
    resource_type, profile = profile_for(ref.resource_type)
    if ref.tenant_id != actor.tenant_id:
        raise ArmError(404, "ResourceNotFound", "Resource was not found")
    scope = parse_scope(ref.scope)
    contract = resolve_operation(
        {"create": "PUT", "update": "PATCH", "delete": "DELETE"}[operation],
        scope.display_id,
        profile["apiVersion"],
    )
    row = await session.get(ArmResource, (actor.tenant_id, scope.key))
    parent = parse_scope(scope.parent_keys[-1])
    action = resource_type + ("/delete" if operation == "delete" else "/write")
    await require_action(session, actor, scope if row else parent, action)
    group = await resolve_scope(
        session, actor.tenant_id, request_scope(str(scope.subscription_id), scope.group)
    )
    if group.provisioning_state == "Deleting":
        raise ArmError(409, "ResourceGroupBeingDeleted", "Resource group deletion is in progress")
    state = await session.get(ProviderResource, (actor.tenant_id, scope.key))
    if row is not None and state is None:
        raise ArmError(
            409, "InvalidOperation", "Inventory-only resources require typed provisioning"
        )
    if row and row.deletion_job_id:
        from mockazure.db.job_model import Job

        active = await session.get(Job, row.deletion_job_id)
        if active and active.state in {"pending", "running"}:
            raise ArmError(409, "OperationInProgress", "Resource has a pending provider operation")
    before = await resource_view(session, row, profile) if row else {}
    if operation == "delete":
        status = 204 if 204 in contract["statusCodes"] else 200
        if not row:
            return MutationResult(status, None, {})
        keys = set(
            await session.scalars(
                select(ArmResource.scope_key).where(
                    ArmResource.tenant_id == actor.tenant_id,
                    (ArmResource.scope_key == scope.key)
                    | ArmResource.scope_key.startswith(scope.key + "/", autoescape=True),
                )
            )
        )
        blocked = await session.scalar(
            select(ResourceRelationship.source_key)
            .where(
                ResourceRelationship.tenant_id == actor.tenant_id,
                ResourceRelationship.target_key.in_(keys),
                ResourceRelationship.source_key.not_in(keys),
            )
            .limit(1)
        )
        if blocked:
            raise ArmError(409, "ResourceInUse", "Other resources still reference this resource")
        await publish(session, actor, scope, action, before, {}, now)
        await session.execute(
            delete(ArmResource).where(
                ArmResource.tenant_id == actor.tenant_id, ArmResource.scope_key.in_(keys)
            )
        )
        return MutationResult(status, None, {})
    if operation == "update" and row is None:
        raise ArmError(404, "ResourceNotFound", "Resource was not found")
    registration = await session.get(
        ProviderRegistration,
        (actor.tenant_id, scope.subscription_id, resource_type.split("/")[0].lower()),
    )
    if not registration or registration.state != "Registered":
        raise ArmError(
            409,
            "MissingSubscriptionRegistration",
            "Register this provider before creating resources",
        )
    validate_wire(body, contract, profile)
    current = configuration(row, profile) if row else None
    value = merge(current or {}, body)
    value = await validate_resource(session, ref, value, profile, current)
    await validate_references(session, ref, value)
    await require_linked_actions(session, actor, ref, value)
    if current is not None and current == value:
        return MutationResult(200, before, {})
    if (
        current
        and profile["family"] == "vault"
        and current["properties"].get("enableRbacAuthorization")
        != value["properties"].get("enableRbacAuthorization")
    ):
        await require_action(session, actor, scope, "Microsoft.Authorization/roleAssignments/write")
    created = row is None
    if created:
        count = await session.scalar(
            select(func.count())
            .select_from(ProviderResource)
            .where(ProviderResource.tenant_id == actor.tenant_id)
        )
        if count >= 10000:
            raise ArmError(
                409, "ResourceLimitExceeded", "Local tenant provider-resource limit reached"
            )
        if profile["family"] == "vault":
            await session.execute(text("SELECT pg_advisory_xact_lock(726385210059)"))
            if await session.scalar(
                select(Vault.name).where(Vault.name == scope.resource_parts[-1].lower())
            ):
                raise ArmError(409, "VaultAlreadyExists", "Vault name is unavailable")
        row = ArmResource(
            tenant_id=actor.tenant_id,
            subscription_id=scope.subscription_id,
            scope_key=scope.key,
            display_id=scope.display_id,
            kind="resource",
            group_key=parse_scope(
                f"/subscriptions/{scope.subscription_id}/resourceGroups/{scope.group}"
            ).key,
            name=scope.resource_parts[-1],
            resource_type=resource_type,
            location=value.get(
                "location", (await resolve_scope(session, actor.tenant_id, parent)).location
            ),
            tags={},
            payload={},
            revision=0,
            created_at=now,
            changed_at=now,
        )
        session.add(row)
    row.tags = value.get("tags", {})
    row.location = value.get("location", row.location)
    row.revision += 1
    row.changed_at = now
    row.payload = {
        key: copy.deepcopy(item) for key, item in value.items() if key not in {"location", "tags"}
    }
    row.payload["properties"]["provisioningState"] = "Succeeded"
    if profile["family"] == "vm":
        row.payload["properties"]["vmId"] = before.get("properties", {}).get("vmId", str(uuid4()))
        row.payload["properties"]["timeCreated"] = row.created_at.isoformat()
    await session.flush()
    if created:
        session.add(
            ProviderResource(
                tenant_id=actor.tenant_id, scope_key=scope.key, family=profile["family"]
            )
        )
        if profile["family"] == "vault":
            session.add(
                Vault(tenant_id=actor.tenant_id, scope_key=scope.key, name=row.name.lower())
            )
        elif profile["family"] == "workspace":
            session.add(
                Workspace(tenant_id=actor.tenant_id, scope_key=scope.key, customer_id=uuid4())
            )
    await session.flush()
    if profile["family"] == "workspace":
        workspace = await session.get(Workspace, (actor.tenant_id, scope.key))
        row.payload = {
            **row.payload,
            "properties": {
                **row.payload["properties"],
                "customerId": str(workspace.customer_id),
                "createdDate": row.created_at.isoformat(),
                "modifiedDate": now.isoformat(),
            },
        }
    if profile["family"] == "vm":
        from mockazure.providers.arm.models import VmState

        await session.flush()
        if await session.get(VmState, (actor.tenant_id, scope.key)) is None:
            session.add(VmState(tenant_id=actor.tenant_id, scope_key=scope.key))
    await replace_references(session, ref, value)
    await session.flush()
    after = await resource_view(session, row, profile)
    await publish(session, actor, scope, action, before, after, now)
    return MutationResult(201 if created and 201 in contract["statusCodes"] else 200, after, {})


async def handle(request, subscription, namespace, resource_path, group=None):
    contract = resolve_operation(
        request.method, request.url.path, request.query_params.get("api-version")
    )
    if "resourceType" not in contract:
        raise ArmError(
            501, "MockAzureOperationNotImplemented", "Provider operation is not modeled here"
        )
    allowed = {"api-version", "$skiptoken"} if contract["collection"] else {"api-version"}
    if (
        len(request.query_params) != len(request.query_params.multi_items())
        or set(request.query_params) - allowed
    ):
        raise ArmError(400, "InvalidQueryParameterValue", "Unsupported provider query")
    resource_type, profile = profile_for(contract["resourceType"])
    scope = request_scope(subscription, group)
    endpoint = request.app.state.settings.advertised_endpoints(request.headers.get("host", ""))
    if contract["collection"]:
        async with provider_transaction(request, scope, mutating=False) as (session, actor, sub):
            grants = await grant_query(session, actor, resource_type + "/read")
            if not await session.scalar(
                grants.where(RoleAssignment.subscription_id == scope.subscription_id).limit(1)
            ):
                raise ArmError(403, "AuthorizationFailed", "No accessible provider scopes")
            sub = await resolve_scope(session, actor.tenant_id, request_scope(subscription))
            state = await read_page(request, session, actor, contract["version"], sub.revision)
            visible = await readable_scopes(session, actor, resource_type + "/read")
            query = select(ArmResource).join(
                ProviderResource,
                (ProviderResource.tenant_id == ArmResource.tenant_id)
                & (ProviderResource.scope_key == ArmResource.scope_key),
            )
            query = query.where(
                ArmResource.tenant_id == actor.tenant_id,
                ArmResource.subscription_id == scope.subscription_id,
                ArmResource.resource_type == resource_type,
                visible,
            )
            if group:
                query = query.where(ArmResource.group_key == scope.key)
            parts = resource_path.split("/")
            if len(parts) > 1:
                parent = parse_scope(
                    scope.display_id + "/providers/" + namespace + "/" + "/".join(parts[:-1])
                )
                await require_action(session, actor, parent, resource_type + "/read")
                query = query.where(
                    ArmResource.scope_key.startswith(parent.key + "/", autoescape=True)
                )
            if state.last_key:
                query = query.where(ArmResource.scope_key > state.last_key[0])
            rows = list(await session.scalars(query.order_by(ArmResource.scope_key).limit(101)))
            result = {
                "value": [
                    await resource_view(session, row, profile, endpoints=endpoint)
                    for row in rows[:100]
                ]
            }
            if len(rows) > 100:
                result["nextLink"] = await next_link(request, session, state, rows[99].scope_key)
            return result
    if not group:
        raise ArmError(400, "InvalidResourceId", "Resource group is required")
    scope = parse_scope(scope.display_id + "/providers/" + namespace + "/" + resource_path)
    if request.method == "GET":
        async with provider_transaction(request, request_scope(subscription), mutating=False) as (
            session,
            actor,
            sub,
        ):
            await require_action(session, actor, scope, resource_type + "/read")
            row = await resolve_scope(session, actor.tenant_id, scope)
            if not await session.get(ProviderResource, (actor.tenant_id, scope.key)):
                raise ArmError(409, "InvalidOperation", "Resource is inventory-only")
            return await resource_view(session, row, profile, endpoints=endpoint)
    supplied = await body(request) if request.method in {"PUT", "PATCH"} else {}
    async with transaction(request, scope) as (session, actor, sub):
        if request.method == "DELETE" and 202 in contract["statusCodes"]:
            from mockazure.providers.arm.provider_job_routes import accepted
            from mockazure.providers.arm.provider_jobs import start_operation

            existing = await session.get(ArmResource, (actor.tenant_id, scope.key))
            if existing is not None:
                return accepted(
                    request,
                    await start_operation(session, actor, scope, "delete", contract["version"]),
                )
        result = await mutate_resource(
            session,
            actor,
            ResourceRef(actor.tenant_id, scope.display_id, resource_type),
            {"PUT": "create", "PATCH": "update", "DELETE": "delete"}[request.method],
            supplied,
            datetime.now(UTC),
        )
        if result.body is None:
            return Response(status_code=result.status, headers=result.headers)
        row = await session.get(ArmResource, (actor.tenant_id, scope.key))
        return JSONResponse(
            await resource_view(session, row, profile, endpoints=endpoint),
            status_code=result.status,
            headers=result.headers,
        )


@router.api_route(
    "/subscriptions/{subscription}/resourcegroups/{group}/providers/{namespace}/{resource_path:path}",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def group_resource(
    subscription: str, group: str, namespace: str, resource_path: str, request: Request
):
    try:
        return await handle(request, subscription, namespace, resource_path, group)
    except (ValueError, KeyError, TypeError):
        raise ArmError(
            400, "InvalidRequestContent", "Invalid or unsupported provider resource"
        ) from None


@router.get("/subscriptions/{subscription}/providers/{namespace}/{resource_path:path}")
async def subscription_resource(
    subscription: str, namespace: str, resource_path: str, request: Request
):
    try:
        return await handle(request, subscription, namespace, resource_path)
    except (ValueError, KeyError, TypeError):
        raise ArmError(
            400, "InvalidRequestContent", "Invalid or unsupported provider resource"
        ) from None

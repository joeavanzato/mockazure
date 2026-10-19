"""Typed provider fixtures restore relationships atomically without replaying operations."""

import copy
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from sqlalchemy import delete, func, select, text, update

from mockazure.admin.errors import AdminError
from mockazure.cloud.events import lock_cloud_state
from mockazure.cloud.models import CloudEvent
from mockazure.cloud.types import ResourceRef
from mockazure.evidence.repository import lock_state
from mockazure.identity.types import IdentityInput
from mockazure.keyvault.models import Vault
from mockazure.keyvault.secret_models import DeletedVault, Secret
from mockazure.monitor.workspaces import Workspace
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import resolve_scope
from mockazure.providers.arm.models import (
    ArmResource,
    ProviderResource,
    ResourceRelationship,
    Subscription,
)
from mockazure.providers.arm.provider_registry import resolve_operation
from mockazure.providers.arm.resource_relationships import replace_references, validate_references
from mockazure.providers.arm.resource_validation import (
    configuration,
    profile_for,
    validate_resource,
    validate_wire,
)
from mockazure.providers.arm.scopes import parse_scope
from mockazure.security.repository import lock_security


def identity(identifier):
    scope = parse_scope(identifier)
    if len(scope.resource_parts) < 4:
        raise ValueError("Provider resource ID required")
    resource_type = scope.resource_parts[1] + "/" + "/".join(scope.resource_parts[2::2])
    resource_type, profile = profile_for(resource_type)
    return scope, resource_type, profile


class ResourceInput(IdentityInput):
    id: str = Field(min_length=1, max_length=2048)
    configuration: dict
    customer_id: UUID | None = None
    vm_id: UUID | None = None
    power_state: Literal["running", "stopped", "deallocated"] | None = None

    @model_validator(mode="after")
    def checked(self):
        scope, resource_type, profile = identity(self.id)
        operation = resolve_operation("PUT", scope.display_id, profile["apiVersion"])
        validate_wire(self.configuration, operation, profile)
        if self.customer_id is not None and profile["family"] != "workspace":
            raise ValueError("Customer identity belongs to workspaces only")
        if (self.vm_id is not None or self.power_state is not None) and profile["family"] != "vm":
            raise ValueError("VM identity belongs to VMs only")
        return self


class ResourceStateInput(IdentityInput):
    resources: list[ResourceInput] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def distinct(self):
        keys = [parse_scope(row.id).key for row in self.resources]
        customers = [row.customer_id for row in self.resources if row.customer_id is not None]
        vaults = [
            parse_scope(row.id).resource_parts[-1].lower()
            for row in self.resources
            if identity(row.id)[2]["family"] == "vault"
        ]
        if (
            len(keys) != len(set(keys))
            or len(customers) != len(set(customers))
            or len(vaults) != len(set(vaults))
        ):
            raise ValueError("Duplicate provider resource identity")
        return self


async def lock_resources(session, tenant):
    await lock_security(session, tenant)
    await lock_state(session, tenant)
    await session.scalars(
        select(Subscription)
        .where(Subscription.tenant_id == tenant)
        .order_by(Subscription.id)
        .with_for_update()
    )
    await session.execute(text("SELECT pg_advisory_xact_lock(726385210059)"))
    await lock_cloud_state(session, tenant)


async def export_provider_resources(session, tenant):
    await lock_resources(session, tenant)
    rows = list(
        await session.scalars(
            select(ArmResource)
            .join(
                ProviderResource,
                (ProviderResource.tenant_id == ArmResource.tenant_id)
                & (ProviderResource.scope_key == ArmResource.scope_key),
            )
            .where(ArmResource.tenant_id == tenant)
            .order_by(ArmResource.scope_key)
            .limit(10001)
        )
    )
    if len(rows) > 10000:
        raise AdminError(
            413, "ResourceStateLimit", "Provider fixture exceeds the local resource limit"
        )
    result = []
    for row in rows:
        resource_type, profile = profile_for(row.resource_type)
        workspace = (
            await session.get(Workspace, (tenant, row.scope_key))
            if profile["family"] == "workspace"
            else None
        )
        from mockazure.providers.arm.models import VmState

        vm = (
            await session.get(VmState, (tenant, row.scope_key))
            if profile["family"] == "vm"
            else None
        )
        result.append(
            ResourceInput(
                id=row.display_id,
                configuration=configuration(row, profile),
                customer_id=workspace.customer_id if workspace else None,
                power_state=vm.power_state if vm else None,
                vm_id=row.payload.get("properties", {}).get("vmId")
                if profile["family"] == "vm"
                else None,
            )
        )
    return ResourceStateInput(resources=result).model_dump(mode="json", exclude_none=True)


async def replace_provider_resources(session, tenant, value: ResourceStateInput):
    await lock_resources(session, tenant)
    current = list(
        await session.scalars(select(ProviderResource).where(ProviderResource.tenant_id == tenant))
    )
    old_keys = {row.scope_key for row in current}
    wanted = {parse_scope(row.id).key for row in value.resources}
    try:
        from mockazure.cloud.lifecycle import retire_operations

        # A domain-only restore must also revoke old provider-worker authority.
        # Its surrounding transaction rolls retirement back if validation fails.
        await retire_operations(session, tenant, include_groups=False)
        for item in value.resources:
            scope, resource_type, profile = identity(item.id)
            group = await resolve_scope(
                session,
                tenant,
                parse_scope(f"/subscriptions/{scope.subscription_id}/resourceGroups/{scope.group}"),
            )
            if group.provisioning_state == "Deleting":
                raise ValueError("Parent deletion is in progress")
            if profile["family"] == "vault":
                found = await session.scalar(
                    select(Vault).where(Vault.name == scope.resource_parts[-1].lower())
                )
                if found and (found.tenant_id != tenant or found.scope_key not in old_keys):
                    raise ValueError("Vault name belongs to another scope")
                retained = await session.scalar(
                    select(DeletedVault).where(
                        DeletedVault.name == scope.resource_parts[-1].lower(),
                        DeletedVault.tenant_id != tenant,
                    )
                )
                if retained:
                    raise ValueError("Vault name is retained by another tenant")
            if item.customer_id:
                found = await session.scalar(
                    select(Workspace).where(Workspace.customer_id == item.customer_id)
                )
                if found and (found.tenant_id != tenant or found.scope_key not in old_keys):
                    raise ValueError("Workspace identity belongs to another scope")
        removed = old_keys - wanted
        if removed:
            outside = await session.scalar(
                select(ResourceRelationship.source_key)
                .where(
                    ResourceRelationship.tenant_id == tenant,
                    ResourceRelationship.target_key.in_(removed),
                    ResourceRelationship.source_key.not_in(old_keys),
                )
                .limit(1)
            )
            if outside:
                raise ValueError("Resources outside this fixture still reference removed targets")
        await session.execute(
            delete(ResourceRelationship).where(
                ResourceRelationship.tenant_id == tenant,
                ResourceRelationship.source_key.in_(old_keys),
            )
        )
        if old_keys:
            await session.execute(
                update(CloudEvent)
                .where(
                    CloudEvent.tenant_id == tenant, func.lower(CloudEvent.resource_id).in_(old_keys)
                )
                .values(publish_eligible=False)
            )
        if removed:
            # Fixture replacement is authoritative removal, not public ARM soft
            # deletion. Secrets no longer have a Vault FK (retained vaults own
            # them too), so explicitly preserve the old cascading cleanup here.
            await session.execute(
                delete(Secret).where(Secret.tenant_id == tenant, Secret.vault_id.in_(removed))
            )
            await session.execute(
                delete(ArmResource).where(
                    ArmResource.tenant_id == tenant, ArmResource.scope_key.in_(removed)
                )
            )
        # Build all prospective rows before resolving references: JSON ordering is irrelevant.
        now = datetime.now(UTC)
        staged = []
        for item in sorted(value.resources, key=lambda row: row.id.count("/")):
            scope, resource_type, profile = identity(item.id)
            config = copy.deepcopy(item.configuration)
            group_key = parse_scope(
                f"/subscriptions/{scope.subscription_id}/resourceGroups/{scope.group}"
            ).key
            group = await session.get(ArmResource, (tenant, group_key))
            parent = await session.get(ArmResource, (tenant, scope.parent_keys[-1]))
            row = await session.get(ArmResource, (tenant, scope.key))
            if row is None:
                row = ArmResource(
                    tenant_id=tenant,
                    subscription_id=scope.subscription_id,
                    scope_key=scope.key,
                    display_id=scope.display_id,
                    kind="resource",
                    group_key=group_key,
                    name=scope.resource_parts[-1],
                    resource_type=resource_type,
                    location=config.get("location", parent.location if parent else group.location),
                    created_at=now,
                    changed_at=now,
                    revision=0,
                    tags={},
                    payload={},
                )
                session.add(row)
            row.payload = {
                k: copy.deepcopy(v) for k, v in config.items() if k not in {"location", "tags"}
            }
            row.payload.setdefault("properties", {})["provisioningState"] = "Succeeded"
            row.tags, row.location = config.get("tags", {}), config.get("location", row.location)
            row.revision += 1
            row.deletion_job_id = None
            row.provisioning_state = "Succeeded"
            row.changed_at = now
            await session.flush()
            if await session.get(ProviderResource, (tenant, scope.key)) is None:
                session.add(
                    ProviderResource(
                        tenant_id=tenant, scope_key=scope.key, family=profile["family"]
                    )
                )
            staged.append((item, scope, resource_type, profile, row, config))
        await session.flush()
        for item, scope, resource_type, profile, row, config in staged:
            ref = ResourceRef(tenant, scope.display_id, resource_type)
            config = await validate_resource(session, ref, config, profile)
            await validate_references(session, ref, config)
            payload = {
                k: copy.deepcopy(v) for k, v in config.items() if k not in {"location", "tags"}
            }
            payload["properties"]["provisioningState"] = "Succeeded"
            if profile["family"] == "workspace":
                workspace = await session.get(Workspace, (tenant, scope.key))
                if workspace is None:
                    workspace = Workspace(
                        tenant_id=tenant,
                        scope_key=scope.key,
                        customer_id=item.customer_id or uuid4(),
                    )
                    session.add(workspace)
                elif item.customer_id and workspace.customer_id != item.customer_id:
                    raise ValueError(
                        "Existing workspace identity cannot be reassigned by a fixture"
                    )
                payload["properties"].update(
                    customerId=str(workspace.customer_id),
                    createdDate=row.created_at.isoformat(),
                    modifiedDate=now.isoformat(),
                )
            elif profile["family"] == "vault":
                if await session.get(Vault, (tenant, scope.key)) is None:
                    session.add(Vault(tenant_id=tenant, scope_key=scope.key, name=row.name.lower()))
            elif profile["family"] == "vm":
                from mockazure.providers.arm.models import VmState

                vm = await session.get(VmState, (tenant, scope.key))
                if vm is None:
                    vm = VmState(tenant_id=tenant, scope_key=scope.key)
                    session.add(vm)
                vm.power_state = item.power_state or "running"

                payload["properties"].update(
                    vmId=str(item.vm_id or uuid4()), timeCreated=row.created_at.isoformat()
                )
            row.payload = payload
            await replace_references(session, ref, config)
        for sub in await session.scalars(
            select(Subscription).where(Subscription.tenant_id == tenant)
        ):
            sub.revision += 1
        await session.flush()
        return await export_provider_resources(session, tenant)
    except (ArmError, ValueError, KeyError, TypeError):
        raise AdminError(
            422, "InvalidResourceState", "Invalid provider configuration, ownership or relationship"
        ) from None


async def reset_provider_resources(session, tenant):
    return await replace_provider_resources(session, tenant, ResourceStateInput())


async def prune_provider_resources(session, tenant, now, limit=500):
    if not 1 <= limit <= 1000 or now.tzinfo is None:
        raise ValueError("Invalid cleanup bounds")
    # Management records do not expire; domain deletion and FK cascades own their cleanup.
    return 0

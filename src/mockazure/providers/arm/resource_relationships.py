"""Relationship references resolve only within the owning tenant/subscription."""

from sqlalchemy import delete, select

from mockazure.cloud.types import ResourceRef
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import resolve_scope
from mockazure.providers.arm.models import ArmResource, ProviderResource, ResourceRelationship
from mockazure.providers.arm.scopes import parse_scope


def reference_values(ref, body):
    scope = parse_scope(ref.scope)
    props = body.get("properties", {})
    kind = ref.resource_type.lower()
    if len(scope.resource_parts) > 4:
        yield "$parent", scope.parent_keys[-1], ref.resource_type.rsplit("/", 1)[0]
    if kind == "microsoft.compute/virtualmachines":
        for index, nic in enumerate(props.get("networkProfile", {}).get("networkInterfaces", [])):
            yield (
                f"networkProfile/networkInterfaces/{index}",
                nic["id"],
                "Microsoft.Network/networkInterfaces",
            )
        storage = props.get("storageProfile", {})
        for index, disk in enumerate([storage.get("osDisk", {}), *storage.get("dataDisks", [])]):
            if disk.get("managedDisk", {}).get("id"):
                yield (
                    f"storageProfile/disks/{index}",
                    disk["managedDisk"]["id"],
                    "Microsoft.Compute/disks",
                )
    if kind in {"microsoft.network/networkinterfaces", "microsoft.network/virtualnetworks/subnets"}:
        if props.get("networkSecurityGroup"):
            yield (
                "networkSecurityGroup",
                props["networkSecurityGroup"]["id"],
                "Microsoft.Network/networkSecurityGroups",
            )
    if kind == "microsoft.network/networkinterfaces":
        for index, configuration in enumerate(props.get("ipConfigurations", [])):
            for key, target_type in (
                ("subnet", "Microsoft.Network/virtualNetworks/subnets"),
                ("publicIPAddress", "Microsoft.Network/publicIPAddresses"),
            ):
                target = configuration.get("properties", {}).get(key)
                if target:
                    yield f"ipConfigurations/{index}/{key}", target["id"], target_type
    if kind == "microsoft.keyvault/vaults":
        for index, rule in enumerate(props.get("networkAcls", {}).get("virtualNetworkRules", [])):
            yield (
                f"networkAcls/virtualNetworkRules/{index}",
                rule["id"],
                "Microsoft.Network/virtualNetworks/subnets",
            )


async def validate_references(session, ref: ResourceRef, body: dict) -> None:
    source = parse_scope(ref.scope)
    expected_location = body.get("location")
    if expected_location is None and len(source.resource_parts) > 4:
        parent = await session.get(ArmResource, (ref.tenant_id, source.parent_keys[-1]))
        expected_location = parent.location if parent else None
    try:
        for path, identifier, target_type in reference_values(ref, body):
            target = parse_scope(identifier)
            if target.subscription_id != source.subscription_id or target.key == source.key:
                raise ValueError("Cross-subscription or self reference")
            row = await resolve_scope(session, ref.tenant_id, target)
            if not isinstance(row, ArmResource) or row.resource_type.lower() != target_type.lower():
                raise ValueError("Reference type mismatch")
            if (
                ref.resource_type.lower().startswith(("microsoft.compute/", "microsoft.network/"))
                and expected_location is not None
                and row.location.lower() != expected_location.lower()
            ):
                raise ValueError("Linked resources must use the same modeled region")
            if row.provisioning_state in {"Deleting", "Deleted"}:
                raise ValueError("Reference is being deleted")
            if await session.get(ProviderResource, (ref.tenant_id, target.key)) is None:
                raise ValueError("Reference has no operational provider state")
            group = await session.get(ArmResource, (ref.tenant_id, row.group_key))
            if group.provisioning_state == "Deleting":
                raise ValueError("Reference parent is being deleted")
            if (
                ref.resource_type.lower() == "microsoft.compute/virtualmachines"
                and path != "$parent"
            ):
                in_use = await session.scalar(
                    select(ResourceRelationship.source_key)
                    .join(
                        ArmResource,
                        (ArmResource.tenant_id == ResourceRelationship.tenant_id)
                        & (ArmResource.scope_key == ResourceRelationship.source_key),
                    )
                    .where(
                        ResourceRelationship.tenant_id == ref.tenant_id,
                        ResourceRelationship.target_key == target.key,
                        ResourceRelationship.source_key != source.key,
                        ArmResource.resource_type == "Microsoft.Compute/virtualMachines",
                    )
                    .limit(1)
                )
                if in_use:
                    raise ValueError("Device is already attached to another VM")
    except (ArmError, KeyError, ValueError, TypeError):
        raise ArmError(
            400,
            "InvalidResourceReference",
            "Reference must resolve to an available local resource of the expected type",
        ) from None


async def replace_references(session, ref, body):
    scope = parse_scope(ref.scope)
    await session.execute(
        delete(ResourceRelationship).where(
            ResourceRelationship.tenant_id == ref.tenant_id,
            ResourceRelationship.source_key == scope.key,
        )
    )
    for path, identifier, expected_type in reference_values(ref, body):
        session.add(
            ResourceRelationship(
                tenant_id=ref.tenant_id,
                source_key=scope.key,
                path=path,
                target_key=parse_scope(identifier).key,
            )
        )


async def require_linked_actions(session, actor, ref, body):
    from mockazure.providers.arm.authorization import require_action

    for path, identifier, target_type in reference_values(ref, body):
        if path == "$parent":
            continue  # The source write already requires child-write permission on this parent.
        if target_type == "Microsoft.Compute/disks":
            action = target_type + "/write"
        elif ref.resource_type == "Microsoft.KeyVault/vaults":
            action = target_type + "/joinViaServiceEndpoint/action"
        else:
            action = target_type + "/join/action"
        try:
            await require_action(session, actor, parse_scope(identifier), action)
        except ArmError as error:
            if error.status == 403:
                raise ArmError(
                    403,
                    "LinkedAuthorizationFailed",
                    "The current identity lacks the linked-resource action",
                ) from None
            raise

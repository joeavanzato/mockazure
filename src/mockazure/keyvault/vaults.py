"""Key Vault management validation; data-plane policy evaluation is a separate layer."""

from ipaddress import ip_network
from uuid import UUID

from sqlalchemy import select

from mockazure.directory.models import Group
from mockazure.identity.models import ServicePrincipal, User


async def validate_vault(session, tenant, properties, before=None):
    if UUID(properties["tenantId"]) != tenant:
        raise ValueError("Vault tenant must match the owning subscription")
    if properties["sku"] not in (
        {"family": "A", "name": "standard"},
        {"family": "A", "name": "premium"},
    ):
        raise ValueError("Unsupported vault SKU")
    if properties.get("enableSoftDelete", True) is not True:
        raise ValueError("Vault soft delete cannot be disabled")
    properties.setdefault("enableSoftDelete", True)
    properties.setdefault("softDeleteRetentionInDays", 90)
    properties.setdefault("enableRbacAuthorization", False)
    properties.setdefault("enablePurgeProtection", False)
    properties.setdefault("accessPolicies", [])
    properties.setdefault("publicNetworkAccess", "Enabled")
    if not 7 <= properties["softDeleteRetentionInDays"] <= 90:
        raise ValueError("Invalid vault retention")
    if before:
        if before["softDeleteRetentionInDays"] != properties["softDeleteRetentionInDays"]:
            raise ValueError("Vault retention is immutable")
        if before.get("enablePurgeProtection") and not properties.get("enablePurgeProtection"):
            raise ValueError("Purge protection cannot be disabled")
    if properties["publicNetworkAccess"] not in {"Enabled", "Disabled"}:
        raise ValueError("Unsupported vault network access mode")
    network = properties.get("networkAcls", {})
    if network.get("defaultAction", "Allow") not in {"Allow", "Deny"} or network.get(
        "bypass", "None"
    ) not in {"AzureServices", "None"}:
        raise ValueError("Unsupported vault network rules")
    for rule in network.get("ipRules", []):
        if ip_network(rule["value"], strict=True).version != 4:
            raise ValueError("Vault IP rules require IPv4")
    policies = properties["accessPolicies"]
    if len(policies) > 1024 or len({p["objectId"].lower() for p in policies}) != len(policies):
        raise ValueError("Invalid or duplicate access policies")
    for policy in policies:
        if UUID(policy["tenantId"]) != tenant or "applicationId" in policy:
            raise ValueError("Only local principal access policies are supported")
        identifier = UUID(policy["objectId"])
        user = await session.scalar(
            select(User.id).where(
                User.tenant_id == tenant, User.id == identifier, User.deleted_at.is_(None)
            )
        )
        app = await session.scalar(
            select(ServicePrincipal.id).where(
                ServicePrincipal.tenant_id == tenant, ServicePrincipal.id == identifier
            )
        )
        group = await session.scalar(
            select(Group.id).where(
                Group.tenant_id == tenant, Group.id == identifier, Group.deleted_at.is_(None)
            )
        )
        if not (user or app or group):
            raise ValueError("Access-policy principal does not exist in this tenant")
        if set(policy["permissions"].get("secrets", [])) - {
            "all",
            "get",
            "list",
            "set",
            "delete",
            "recover",
            "backup",
            "restore",
            "purge",
        }:
            raise ValueError("Unknown secret permission")

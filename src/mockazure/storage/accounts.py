"""Storage account mutations; callers hold security/evidence/subscription locks."""

import base64
import copy
import hmac
import re
import secrets
from datetime import datetime
from ipaddress import ip_address, ip_network
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.cloud.events import append_service_event
from mockazure.cloud.models import ProviderRegistration
from mockazure.cloud.types import MutationResult, ResourceRef, ServiceEvent
from mockazure.identity.types import PrincipalContext
from mockazure.providers.arm.authorization import require_action
from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.facts import append_arm_fact
from mockazure.providers.arm.inventory import (
    checked_tags,
    clean_public,
    inventory_view,
    resolve_scope,
)
from mockazure.providers.arm.models import ArmResource, Subscription
from mockazure.providers.arm.scopes import parse_scope
from mockazure.storage.models import StorageAccount

TYPE = "Microsoft.Storage/storageAccounts"
DEFAULTS = {
    "allowSharedKeyAccess": True,
    "allowBlobPublicAccess": False,
    "allowCrossTenantReplication": False,
    "supportsHttpsTrafficOnly": True,
    "minimumTlsVersion": "TLS1_2",
    "publicNetworkAccess": "Enabled",
    "defaultToOAuthAuthentication": False,
    "networkAcls": {
        "defaultAction": "Allow",
        "bypass": "None",
        "ipRules": [],
        "virtualNetworkRules": [],
    },
}
PROPERTIES = set(DEFAULTS) | {
    "accessTier",
    "isHnsEnabled",
    "isNfsV3Enabled",
    "isSftpEnabled",
    "isLocalUserEnabled",
    "largeFileSharesState",
}
KINDS = {"Storage", "StorageV2", "BlobStorage", "BlockBlobStorage", "FileStorage"}
STANDARD = {"Standard_" + s for s in ("LRS", "GRS", "RAGRS", "ZRS", "GZRS", "RAGZRS")}
PREMIUM = {"Premium_LRS", "Premium_ZRS"}


def account_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]{3,24}", name):
        raise ValueError("Storage account names require 3-24 lowercase letters or digits")
    return name


def cipher(session):
    return Fernet(session.info["storage_cipher"])


def new_key(session):
    return cipher(session).encrypt(base64.b64encode(secrets.token_bytes(64))).decode("ascii")


async def resolve_account(session: AsyncSession, tenant_id: UUID, name: str) -> ResourceRef:
    account_name(name)
    row = await session.scalar(
        select(StorageAccount).where(
            StorageAccount.tenant_id == tenant_id, StorageAccount.name == name
        )
    )
    if row is None:
        raise ArmError(404, "ResourceNotFound", "Storage account was not found")
    return ResourceRef(tenant_id, row.scope_key, TYPE)


async def key_matches(session, tenant_id, name, supplied):
    ref = await resolve_account(session, tenant_id, name)
    row = await session.get(StorageAccount, (tenant_id, ref.scope))
    # Both candidates are compared; rotation replaces exactly one encrypted key.
    matches = [
        hmac.compare_digest(
            cipher(session).decrypt(getattr(row, key).encode()), supplied.encode("ascii")
        )
        for key in ("key1", "key2")
    ]
    return any(matches)


def keys_view(session, row):
    return {
        "keys": [
            {
                "keyName": key,
                "value": cipher(session).decrypt(getattr(row, key).encode()).decode("ascii"),
                "permissions": "Full",
                "creationTime": getattr(row, key + "_created").isoformat(),
            }
            for key in ("key1", "key2")
        ]
    }


def network_allowed(properties, observed_peer, headers):
    # Header claims are deliberately not used. Reverse-proxy/private-fabric trust is not modeled.
    if properties.get("publicNetworkAccess", "Enabled") != "Enabled":
        return False
    rules = properties.get("networkAcls", {})
    if rules.get("defaultAction", "Allow") == "Allow":
        return True
    try:
        address = ip_address(observed_peer)
        return any(
            address in ip_network(rule["value"], strict=False)
            for rule in rules.get("ipRules", []) + rules.get("ipv6Rules", [])
        )
    except (ValueError, TypeError):
        return False


def checked_network(value):
    validate_arm_payload("NetworkRuleSet", value, namespace="storage")
    if set(value) - {
        "defaultAction",
        "bypass",
        "ipRules",
        "ipv6Rules",
        "virtualNetworkRules",
        "resourceAccessRules",
    }:
        raise ValueError("Unsupported network rule")
    if value["defaultAction"] not in {"Allow", "Deny"}:
        raise ValueError("Unsupported network default")
    bypass = value.get("bypass", "None").split(",")
    if any(item.strip() not in {"None", "Logging", "Metrics", "AzureServices"} for item in bypass):
        raise ValueError("Unsupported network bypass")
    for key, version in (("ipRules", 4), ("ipv6Rules", 6)):
        if len(value.get(key, [])) > 200:
            raise ValueError("Too many IP rules")
        for rule in value.get(key, []):
            if (
                rule.get("action", "Allow") != "Allow"
                or ip_network(rule["value"], strict=True).version != version
            ):
                raise ValueError("Invalid IP rule")
    return copy.deepcopy(value)


def checked_payload(value, current=None, partial=False):
    validate_arm_payload(
        "StorageAccountUpdateParameters" if partial else "StorageAccountCreateParameters",
        value,
        namespace="storage",
    )
    clean_public(value)
    if set(value) - {"sku", "kind", "location", "tags", "properties"}:
        raise ValueError("Unsupported account field")
    result = copy.deepcopy(current or {"properties": DEFAULTS, "tags": {}})
    result.update({k: copy.deepcopy(v) for k, v in value.items() if k != "properties"})
    supplied = value.get("properties", {})
    if set(supplied) - PROPERTIES:
        raise ValueError("Unsupported account property")
    result.setdefault("properties", {}).update(copy.deepcopy(supplied))
    props = result["properties"]
    if any(
        props.get(key, False)
        for key in ("isHnsEnabled", "isNfsV3Enabled", "isSftpEnabled", "isLocalUserEnabled")
    ):
        raise ValueError("Hierarchical, SMB/NFS and SFTP protocol features are not executable")
    if props.get("minimumTlsVersion") not in {"TLS1_0", "TLS1_1", "TLS1_2"}:
        raise ValueError("Unsupported TLS minimum")
    if props.get("publicNetworkAccess") not in {"Enabled", "Disabled"}:
        raise ValueError("Network security perimeter is unsupported")
    props["networkAcls"] = checked_network(props["networkAcls"])
    if result["kind"] not in KINDS:
        raise ValueError("Unsupported Storage kind")
    allowed_skus = PREMIUM if result["kind"] in {"FileStorage", "BlockBlobStorage"} else STANDARD
    if set(result["sku"]) != {"name"} or result["sku"]["name"] not in allowed_skus:
        raise ValueError("Unsupported Storage SKU")
    if result["location"].lower() not in {"eastus", "westus2"}:
        raise ValueError("Unsupported modeled location")
    if "accessTier" in props and props["accessTier"] not in {"Hot", "Cool", "Cold", "Premium"}:
        raise ValueError("Unsupported access tier")
    if result["kind"] == "BlobStorage" and "accessTier" not in props:
        raise ValueError("BlobStorage requires accessTier")
    if "largeFileSharesState" in props and props["largeFileSharesState"] not in {
        "Enabled",
        "Disabled",
    }:
        raise ValueError("Invalid largeFileSharesState")
    tags = checked_tags(result.get("tags", {}))
    if len(tags) > 15 or any(len(key) > 128 for key in tags):
        raise ValueError("Storage tag limits exceeded")
    result["tags"] = tags
    if current:
        if (
            result["kind"] != current["kind"]
            or result["location"].lower() != current["location"].lower()
        ):
            raise ArmError(
                409, "InvalidOperation", "Kind and location cannot change in this profile"
            )
        if (
            current["properties"].get("largeFileSharesState") == "Enabled"
            and props.get("largeFileSharesState") != "Enabled"
        ):
            raise ValueError("Large file shares cannot be disabled")
    return result


async def record(session, actor, scope, operation, before, after, now, payload=None):
    if before == after:
        return
    sub = await session.get(Subscription, scope.subscription_id)
    sub.revision += 1
    identifier = uuid4()
    await append_arm_fact(session, actor, operation, scope, before, after, now, identifier)
    await append_service_event(
        session,
        ServiceEvent(
            identifier,
            actor.tenant_id,
            scope.display_id,
            "arm",
            "Administrative",
            operation,
            now,
            payload or {"resourceType": TYPE},
        ),
    )


async def mutate_account(
    session: AsyncSession,
    actor: PrincipalContext,
    resource_id: str,
    operation: str,
    body: dict,
    now: datetime,
) -> MutationResult:
    scope = parse_scope(resource_id)
    if len(scope.resource_parts) != 4 or scope.resource_parts[1:3] != (
        "Microsoft.Storage",
        "storageAccounts",
    ):
        raise ValueError("Storage account scope required")
    name = account_name(scope.resource_parts[-1])
    row = await session.get(ArmResource, (actor.tenant_id, scope.key))
    group_scope = parse_scope(scope.parent_keys[-1])
    action = (
        TYPE
        + "/"
        + (
            "regenerateKey/action"
            if operation == "rotate"
            else "delete"
            if operation == "delete"
            else "write"
        )
    )
    await require_action(session, actor, scope if row else group_scope, action)
    group = await resolve_scope(session, actor.tenant_id, group_scope)
    if group.provisioning_state == "Deleting":
        raise ArmError(409, "ResourceGroupBeingDeleted", "Resource group deletion is in progress")
    before = inventory_view(row) if row else {}
    account = await session.get(StorageAccount, (actor.tenant_id, scope.key))
    if operation == "delete":
        if row:
            await record(session, actor, scope, action, before, {}, now)
            await session.delete(row)
            await session.flush()
        return MutationResult(204, None, {})
    if row and account is None:
        raise ArmError(
            409,
            "InvalidOperation",
            "Imported inventory has no operational account; recreate through the provider",
        )
    if operation == "rotate":
        if not account:
            raise ArmError(404, "ResourceNotFound", "Storage account was not found")
        validate_arm_payload("StorageAccountRegenerateKeyParameters", body, namespace="storage")
        if set(body) != {"keyName"} or body["keyName"] not in {"key1", "key2"}:
            raise ValueError("A modeled keyName is required")
        key = body["keyName"]
        previous_time = getattr(account, key + "_created").isoformat()
        setattr(account, key, new_key(session))
        setattr(account, key + "_created", now)
        await record(
            session,
            actor,
            scope,
            action,
            {"keyName": key, "creationTime": previous_time},
            {"keyName": key, "creationTime": now.isoformat()},
            now,
            {"keyName": key},
        )
        return MutationResult(200, keys_view(session, account), {})
    if operation not in {"create", "update"}:
        raise ValueError("Unsupported Storage mutation")
    if operation == "update" and not row:
        raise ArmError(404, "ResourceNotFound", "Storage account was not found")
    registration = await session.get(
        ProviderRegistration, (actor.tenant_id, scope.subscription_id, "microsoft.storage")
    )
    if not registration or registration.state != "Registered":
        raise ArmError(
            409,
            "MissingSubscriptionRegistration",
            "Register Microsoft.Storage before creating accounts",
        )
    current = (
        {
            "kind": row.payload["kind"],
            "sku": row.payload["sku"],
            "location": row.location,
            "tags": row.tags,
            "properties": {k: v for k, v in row.payload["properties"].items() if k in PROPERTIES},
        }
        if row
        else None
    )
    value = checked_payload(body, current, partial=operation == "update")
    if not row:
        # Account names are instance-global; credentials remain tenant-scoped.
        await session.execute(text("SELECT pg_advisory_xact_lock(726385210058)"))
        if await session.scalar(select(StorageAccount.name).where(StorageAccount.name == name)):
            raise ArmError(409, "StorageAccountAlreadyTaken", "Storage account name is unavailable")
        if (
            await session.scalar(
                select(func.count())
                .select_from(StorageAccount)
                .where(StorageAccount.tenant_id == actor.tenant_id)
            )
            >= 1000
        ):
            raise ArmError(409, "StorageAccountLimitExceeded", "Local tenant account limit reached")
        row = ArmResource(
            tenant_id=actor.tenant_id,
            subscription_id=scope.subscription_id,
            scope_key=scope.key,
            display_id=scope.display_id,
            kind="resource",
            group_key=group_scope.key,
            name=name,
            resource_type=TYPE,
            location=value["location"],
            tags=value["tags"],
            payload={},
            created_at=now,
            changed_at=now,
        )
        session.add(row)
        await session.flush()
        account = StorageAccount(
            tenant_id=actor.tenant_id,
            scope_key=scope.key,
            name=name,
            key1=new_key(session),
            key2=new_key(session),
            key1_created=now,
            key2_created=now,
            services={},
        )
        session.add(account)
    props = value["properties"] | {
        "provisioningState": "Succeeded",
        "creationTime": row.created_at.isoformat(),
        "primaryLocation": row.location,
        "statusOfPrimary": "available",
    }
    row.payload = {"kind": value["kind"], "sku": value["sku"], "properties": props}
    row.tags = value["tags"]
    after = inventory_view(row)
    if before != after:
        row.revision += 1
        row.changed_at = now
    await record(session, actor, scope, action, before, after, now)
    await session.flush()
    return MutationResult(200, after, {})

"""Published provider shapes plus explicit executable local profiles."""

import copy
import json
import re
from ipaddress import ip_address, ip_network

from sqlalchemy import select

from mockazure.keyvault.vaults import validate_vault
from mockazure.providers.arm.contracts import DATA, check, document, manifest, object_shape, resolve
from mockazure.providers.arm.inventory import checked_tags, clean_public
from mockazure.providers.arm.models import ArmResource
from mockazure.providers.arm.scopes import parse_scope


def profiles():
    return json.loads((DATA / "resource-profiles.json").read_text(encoding="utf-8"))


def profile_for(resource_type):
    for name, profile in profiles().items():
        if name.lower() == resource_type.lower():
            return name, profile
    raise ValueError("Resource type is not modeled")


def writable(value, schema, source):
    schema, source = resolve(schema, source)
    if isinstance(value, dict):
        properties, required, additional = object_shape(schema, source)
        for key, item in value.items():
            if key in properties:
                node, origin = properties[key]
                if resolve(node, origin)[0].get("readOnly") or node.get("readOnly"):
                    raise ValueError("Read-only provider field cannot be written")
                writable(item, node, origin)
    elif isinstance(value, list):
        for item in value:
            writable(item, schema.get("items", {}), source)


def strip_readonly(value, schema, source):
    schema, source = resolve(schema, source)
    if isinstance(value, dict):
        properties, required, additional = object_shape(schema, source)
        result = {}
        for key, item in value.items():
            if key in properties:
                node, origin = properties[key]
                if not node.get("readOnly") and not resolve(node, origin)[0].get("readOnly"):
                    result[key] = strip_readonly(item, node, origin)
            else:
                result[key] = copy.deepcopy(item)
        return result
    if isinstance(value, list):
        return [strip_readonly(item, schema.get("items", {}), source) for item in value]
    return copy.deepcopy(value)


def configuration(row, profile):
    source = manifest()["roots"][profile["schemaNamespace"]]
    schema = document(source)["definitions"][profile["responseType"]]
    result = strip_readonly(row.payload, schema, source)
    result.pop("id", None)
    result.pop("name", None)
    result.pop("type", None)
    if profile["locationRequired"]:
        result["location"] = row.location
        result["tags"] = copy.deepcopy(row.tags)
    for child in profile["children"]:
        result.get("properties", {}).pop(child, None)
    return result


def merge(current, supplied):
    result = copy.deepcopy(current)
    for key, value in supplied.items():
        result[key] = (
            merge(result[key], value)
            if isinstance(result.get(key), dict) and isinstance(value, dict)
            else copy.deepcopy(value)
        )
    # ARM tags replace rather than merge.
    if "tags" in supplied:
        result["tags"] = copy.deepcopy(supplied["tags"])
    return result


def validate_wire(value, operation, profile):
    source = manifest()["roots"][profile["schemaNamespace"]]
    if (
        not isinstance(value, dict)
        or len(json.dumps(value, allow_nan=False).encode()) > 2 * 1024 * 1024
    ):
        raise ValueError("Provider payload exceeds the local limit")
    check(value, operation["requestSchema"], source)
    writable(value, operation["requestSchema"], source)
    clean_public(value)
    if set(value) - {"location", "tags", "properties", "sku"}:
        raise ValueError("Provider field is outside the local profile")
    if set(value.get("properties", {})) - set(profile["properties"]):
        raise ValueError("Provider property is outside the local profile")


def prefixes(properties):
    values = properties.get("addressPrefixes", [])
    if "addressPrefix" in properties:
        if values:
            raise ValueError("Choose addressPrefix or addressPrefixes")
        values = [properties["addressPrefix"]]
    if not values or len(values) > 8:
        raise ValueError("A bounded address prefix list is required")
    ranges = [ip_network(value, strict=True) for value in values]
    if any(
        a.overlaps(b)
        for i, a in enumerate(ranges)
        for b in ranges[i + 1 :]
        if a.version == b.version
    ):
        raise ValueError("Address prefixes overlap")
    return ranges


async def validate_resource(session, ref, value, profile, current=None):
    scope = parse_scope(ref.scope)
    family, name = profile["family"], scope.resource_parts[-1]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", name):
        raise ValueError("Invalid resource name")
    if family == "vault" and (
        not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{1,22}[A-Za-z0-9]", name) or "--" in name
    ):
        raise ValueError("Invalid vault name")
    if family == "workspace" and (not 4 <= len(name) <= 63 or "_" in name or "." in name):
        raise ValueError("Invalid workspace name")
    if profile["locationRequired"]:
        if value.get("location", "").lower() not in {"eastus", "westus2"}:
            raise ValueError("Unsupported modeled location")
        if current and value["location"].lower() != current["location"].lower():
            raise ValueError("Resource location is immutable")
        value["tags"] = checked_tags(value.get("tags", {}))
    props = value.setdefault("properties", {})
    if family == "vault":
        await validate_vault(
            session, ref.tenant_id, props, current.get("properties") if current else None
        )
    elif family == "workspace":
        props.setdefault("sku", {"name": "PerGB2018"})
        props.setdefault("retentionInDays", 30)
        if props["sku"] != {"name": "PerGB2018"} or not 30 <= props["retentionInDays"] <= 730:
            raise ValueError("Unsupported workspace SKU or retention")
        for key in ("publicNetworkAccessForIngestion", "publicNetworkAccessForQuery"):
            props.setdefault(key, "Enabled")
            if props[key] not in {"Enabled", "Disabled"}:
                raise ValueError("Unsupported workspace network setting")
        if set(props.get("features", {})) - {"enableLogAccessUsingOnlyResourcePermissions"}:
            raise ValueError("Unsupported workspace feature")
        capping = props.get("workspaceCapping", {})
        if set(capping) - {"dailyQuotaGb"} or capping.get("dailyQuotaGb", -1) < -1:
            raise ValueError("Unsupported workspace cap")
    elif family == "vm":
        if props.get("hardwareProfile", {}).get("vmSize") not in {
            "Standard_B1s",
            "Standard_B2s",
            "Standard_D2s_v5",
        }:
            raise ValueError("Unsupported modeled VM size")
        storage = props.get("storageProfile", {})
        disk = storage.get("osDisk", {})
        if disk.get("createOption") not in {"Attach", "FromImage"}:
            raise ValueError("VM requires an attached or image-backed OS disk")
        if disk["createOption"] == "Attach" and not disk.get("managedDisk", {}).get("id"):
            raise ValueError("Attach requires an existing managed disk")
        if disk["createOption"] == "FromImage" and not storage.get("imageReference"):
            raise ValueError("FromImage requires image metadata")
        if "customData" in props.get("osProfile", {}) or "adminPassword" in props.get(
            "osProfile", {}
        ):
            raise ValueError("Credential/custom-data payloads are unsupported")
        if len(props.get("networkProfile", {}).get("networkInterfaces", [])) > 8:
            raise ValueError("Too many VM interfaces")
        for disk in storage.get("dataDisks", []):
            if disk.get("createOption") != "Attach" or not disk.get("managedDisk", {}).get("id"):
                raise ValueError("Data disks require existing managed disk attachments")
    elif family == "extension":
        if not all(
            isinstance(props.get(k), str) and props[k]
            for k in ("publisher", "type", "typeHandlerVersion")
        ):
            raise ValueError("Extension publisher, type and handler version are required")
        # Settings are inventory only. No command is evaluated or fetched.
    elif family == "disk":
        value.setdefault("sku", {"name": "Standard_LRS"})
        if set(value["sku"]) != {"name"} or value["sku"]["name"] not in {
            "Standard_LRS",
            "StandardSSD_LRS",
            "Premium_LRS",
            "Premium_ZRS",
        }:
            raise ValueError("Unsupported disk SKU")
        if (
            props.get("creationData") != {"createOption": "Empty"}
            or not 1 <= props.get("diskSizeGB", 0) <= 32767
        ):
            raise ValueError("Only bounded empty disk provisioning is modeled")
        if props.get("osType", "Linux") not in {"Linux", "Windows"}:
            raise ValueError("Unsupported disk OS")
    elif family == "vnet":
        spaces = prefixes(props.get("addressSpace", {}))
        children = await session.scalars(
            select(ArmResource).where(
                ArmResource.tenant_id == ref.tenant_id,
                ArmResource.scope_key.startswith(scope.key + "/subnets/"),
                ArmResource.resource_type == "Microsoft.Network/virtualNetworks/subnets",
            )
        )
        for child in children:
            for child_range in prefixes(child.payload["properties"]):
                if not any(
                    child_range.version == space.version and child_range.subnet_of(space)
                    for space in spaces
                ):
                    raise ValueError("VNet update would exclude an existing subnet")
        for server in props.get("dhcpOptions", {}).get("dnsServers", []):
            ip_address(server)
        if props.get("enableDdosProtection") or props.get("enableVmProtection"):
            raise ValueError("DDoS/VM protection is not modeled")
    elif family == "subnet":
        ranges = prefixes(props)
        parent = await session.get(ArmResource, (ref.tenant_id, scope.parent_keys[-1]))
        if parent is None:
            raise ValueError("Subnet parent does not exist")
        spaces = prefixes(parent.payload["properties"]["addressSpace"])
        if any(
            not any(item.version == space.version and item.subnet_of(space) for space in spaces)
            for item in ranges
        ):
            raise ValueError("Subnet is outside its VNet")
        siblings = await session.scalars(
            select(ArmResource).where(
                ArmResource.tenant_id == ref.tenant_id,
                ArmResource.resource_type == ref.resource_type,
                ArmResource.scope_key != scope.key,
                ArmResource.scope_key.startswith(scope.parent_keys[-1] + "/subnets/"),
            )
        )
        for sibling in siblings:
            if any(
                a.overlaps(b)
                for a in ranges
                for b in prefixes(sibling.payload["properties"])
                if a.version == b.version
            ):
                raise ValueError("Subnet ranges overlap")
    elif family == "nic":
        configs = props.get("ipConfigurations", [])
        if not 1 <= len(configs) <= 8 or len({c.get("name") for c in configs}) != len(configs):
            raise ValueError("NIC requires uniquely named IP configurations")
        for config in configs:
            cp = config.get("properties", {})
            if not cp.get("subnet", {}).get("id"):
                raise ValueError("IP configuration requires a local subnet")
            if cp.get("privateIPAllocationMethod", "Dynamic") not in {"Dynamic", "Static"}:
                raise ValueError("Unsupported private allocation method")
            if cp.get("privateIPAllocationMethod") == "Static":
                address = ip_address(cp.get("privateIPAddress", ""))
                subnet = await session.get(
                    ArmResource, (ref.tenant_id, parse_scope(cp["subnet"]["id"]).key)
                )
                if subnet is None or not any(
                    address in net for net in prefixes(subnet.payload["properties"])
                ):
                    raise ValueError("Static IP is outside the referenced subnet")
    elif family == "publicip":
        value.setdefault("sku", {"name": "Standard"})
        if (
            value["sku"].get("name") != "Standard"
            or value["sku"].get("tier", "Regional") != "Regional"
        ):
            raise ValueError("Only Standard Regional public IP inventory is modeled")
        props.setdefault("publicIPAllocationMethod", "Static")
        props.setdefault("publicIPAddressVersion", "IPv4")
        if props["publicIPAllocationMethod"] != "Static" or props["publicIPAddressVersion"] not in {
            "IPv4",
            "IPv6",
        }:
            raise ValueError("Unsupported public allocation profile")
    elif family == "rule":
        if (
            not 100 <= props["priority"] <= 4096
            or props["direction"] not in {"Inbound", "Outbound"}
            or props["access"] not in {"Allow", "Deny"}
        ):
            raise ValueError("Invalid security rule")
        if props["protocol"] not in {"Tcp", "Udp", "Icmp", "Esp", "Ah", "*"}:
            raise ValueError("Invalid security protocol")
        for side in ("source", "destination"):
            for suffix in ("PortRange", "AddressPrefix"):
                single = side + suffix
                many = side + ("PortRanges" if suffix == "PortRange" else "AddressPrefixes")
                if bool(props.get(single)) == bool(props.get(many)):
                    raise ValueError("Choose one singular or plural security range")
        siblings = await session.scalars(
            select(ArmResource).where(
                ArmResource.tenant_id == ref.tenant_id,
                ArmResource.scope_key.startswith(scope.parent_keys[-1] + "/securityrules/"),
                ArmResource.scope_key != scope.key,
            )
        )
        if any(
            r.payload["properties"].get("priority") == props["priority"]
            and r.payload["properties"].get("direction") == props["direction"]
            for r in siblings
        ):
            raise ValueError("Security priority/direction must be unique")
    return value

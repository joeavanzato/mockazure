"""Connected directory, Intune and Azure estate observations and setup recipes."""

from mockazure.population.generators.business import Business, edge, linked
from mockazure.population.generators.cloud_resources import resources
from mockazure.population.generators.schema_defaults import payload
from mockazure.population.types import Mutation

VERSION = "business-estate-v1"
FAMILIES = {
    "groups": "directory.groups",
    "directory-devices": "directory.devices",
    "managed-devices": "intune.managedDevice",
    "detected-apps": "intune.detectedApp",
    "software-links": "intune.relationships",
    "device-configurations": "intune.deviceConfiguration",
    "compliance-policies": "intune.deviceCompliancePolicy",
    "compliance-statuses": "intune.deviceComplianceDeviceStatus",
    "configuration-statuses": "intune.deviceConfigurationDeviceStatus",
    "business-applications": "identity.clients",
    "azure-inventory": "arm.inventory",
    "azure-blob": "storage.blobs",
    "azure-file": "storage.files",
}
SCOPE = "deviceManagement"


def item(g, kind, i):
    user = g.person(i)
    device = g.id("directory-device", i)
    managed = g.id("managed-device", i)
    if kind in {"azure-blob", "azure-file"}:
        from mockazure.population.generators import recipes

        plane = kind.removeprefix("azure-")
        result = recipes.generate("recipe." + plane, g.parameters, g.refs, offset=i, limit=1)[0]
        result.payload["variables"][plane] = "project-review.docx"
        return result.model_copy(update={"logical_key": f"business.{kind}:{i}"})
    if kind == "groups":
        return Mutation(
            operation="import",
            logical_key=f"business.groups:{i}",
            payload={
                "id": g.id("project-group", i),
                "displayName": f"Project {i + 1}",
                "mailNickname": f"project-{g.label}-{i}",
                "description": f"Project {i + 1} collaboration",
                "createdDateTime": g.time(),
                "mailEnabled": False,
                "securityEnabled": True,
            },
        )
    if kind == "directory-devices":
        return Mutation(
            operation="import-owned",
            logical_key=f"business.{kind}:{i}",
            payload={
                "owner_id": user["id"],
                "payload": payload(
                    "device",
                    device,
                    reference_time=g.time(),
                    deviceId=g.id("device", i),
                    displayName=f"BUS-{g.label}-{i:05d}",
                    accountEnabled=True,
                    operatingSystem="Windows",
                    operatingSystemVersion="10.0.19045",
                    trustType="AzureAd",
                ),
            },
        )
    if kind == "managed-devices":
        value = payload(
            "managedDevice",
            managed,
            reference_time=g.time(i),
            azureADDeviceId=g.id("device", i),
            userId=user["id"],
            userPrincipalName=user["userPrincipalName"],
            userDisplayName=user["displayName"],
            deviceName=f"BUS-{g.label}-{i:05d}",
            operatingSystem="Windows",
            osVersion="10.0.19045",
            serialNumber=f"SYN-{g.label}-{i:06d}",
            manufacturer="MockAzure",
            model="Synthetic workstation",
            managedDeviceOwnerType="company",
            managementAgent="mdm",
            complianceState="noncompliant" if i % 10 == 0 else "compliant",
            lastSyncDateTime=g.reference.isoformat(),
            enrolledDateTime=g.time(),
        )
        return linked(
            kind,
            i,
            SCOPE,
            value,
            [edge("user", user["id"], "managedDevices", "managedDevice", managed)],
        )
    if kind == "detected-apps":
        value = payload(
            "detectedApp",
            g.id("software", i),
            displayName=("Office suite", "Source tools")[i % 2],
            version="1.0." + str(i),
            publisher="MockAzure Synthetic",
            platform="windows",
            deviceCount=(g.users + 1 - i) // 2,
            sizeInByte=1024 * 1024 * (i + 1),
        )
        return Mutation(
            operation="import", logical_key=f"business.{kind}:{i}", parent=SCOPE, payload=value
        )
    if kind == "software-links":
        return Mutation(
            operation="link",
            logical_key=f"business.{kind}:{i}",
            parent=SCOPE,
            payload=edge(
                "detectedApp", g.id("software", i % 2), "managedDevices", "managedDevice", managed
            ),
        )
    if kind in {"device-configurations", "compliance-policies"}:
        configuration = kind == "device-configurations"
        typ = "windows10GeneralConfiguration" if configuration else "windows10CompliancePolicy"
        value = payload(
            typ,
            g.id(kind, i),
            reference_time=g.time(),
            displayName=f"Business {kind} {i + 1}",
            description="Observed local configuration; no device execution",
            version=1,
            **(
                {"defenderRequireRealTimeMonitoring": True}
                if configuration
                else {"bitLockerEnabled": True}
            ),
        )
        return Mutation(
            operation="import", logical_key=f"business.{kind}:{i}", parent=SCOPE, payload=value
        )
    if kind in {"compliance-statuses", "configuration-statuses"}:
        configuration = kind == "configuration-statuses"
        typ = "deviceConfigurationDeviceStatus" if configuration else "deviceComplianceDeviceStatus"
        source_kind = "deviceConfiguration" if configuration else "deviceCompliancePolicy"
        policy = g.id("device-configurations" if configuration else "compliance-policies", i % 2)
        value = payload(
            typ,
            g.id(kind, i),
            reference_time=g.time(i),
            deviceDisplayName=f"BUS-{g.label}-{i:05d}",
            deviceModel="Synthetic workstation",
            userName=user["displayName"],
            userPrincipalName=user["userPrincipalName"],
            lastReportedDateTime=g.reference.isoformat(),
            status="nonCompliant" if i % 10 == 0 else "compliant",
        )
        return linked(
            kind, i, SCOPE, value, [edge(source_kind, policy, "deviceStatuses", typ, value["id"])]
        )
    if kind == "business-applications":
        return Mutation(
            operation="provision",
            logical_key=f"business.{kind}:{i}",
            payload={
                "id": "project-app-" + g.id("application", i),
                "display_name": f"Project {i + 1} local application",
                "owner_user_id": user["id"],
            },
        )
    if kind == "azure-inventory":
        from mockazure.providers.arm.scopes import parse_scope

        project, ordinal = divmod(i, 8)
        row = resources(g.refs["group"], str(g.tenant), g.label + "-" + str(project))[ordinal]
        return Mutation(
            operation="import",
            logical_key=f"business.{kind}:{i}",
            payload={
                **row["body"],
                "id": row["id"],
                "name": "/".join(parse_scope(row["id"]).resource_parts[3::2]),
                "type": row["type"],
                "location": "eastus",
                "tags": {
                    "dataset": g.namespace,
                    "ownerObjectId": g.person(project)["id"],
                    "project": f"Project {project + 1}",
                    "provenance": "fixture",
                },
            },
        )
    raise ValueError("Unknown estate generator")


def generate(name, parameters, refs, *, offset, limit):
    kind = name.removeprefix("business.")
    g = Business(parameters, refs, offset, limit)
    maximum = (
        1
        if kind in {"azure-blob", "azure-file"}
        else g.projects * 8
        if kind == "azure-inventory"
        else g.projects
        if kind in {"groups", "business-applications"}
        else 2
        if kind in {"detected-apps", "device-configurations", "compliance-policies"}
        else g.users
    )
    if kind not in FAMILIES or g.count > maximum:
        raise ValueError("Estate count exceeds the declared organization inventory")
    return [item(g, kind, i) for i in g.indexes]

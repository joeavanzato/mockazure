"""Acquire immutable official ARM Swagger source and its schema dependencies."""

import hashlib
import html
import json
import posixpath
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src/mockazure/providers/arm/data"
REVISION = "ae348cc71f54a61f5d17472052cfef279a23db6b"
BASE = f"https://raw.githubusercontent.com/Azure/azure-rest-api-specs/{REVISION}/"
SOURCES = {
    "resources": "specification/resources/resource-manager/Microsoft.Resources/resources/"
    "stable/2021-04-01/resources.json",
    "resources2024": "specification/resources/resource-manager/Microsoft.Resources/resources/"
    "stable/2024-07-01/resources.json",
    "subscriptions": "specification/resources/resource-manager/Microsoft.Resources/subscriptions/"
    "stable/2021-01-01/subscriptions.json",
    "subscriptions2020": "specification/resources/resource-manager/"
    "Microsoft.Resources/subscriptions/stable/2020-01-01/subscriptions.json",
    "assignments": "specification/authorization/resource-manager/Microsoft.Authorization/"
    "Authorization/stable/2022-04-01/authorization-RoleAssignmentsCalls.json",
    "definitions": "specification/authorization/resource-manager/Microsoft.Authorization/"
    "Authorization/stable/2022-04-01/authorization-RoleDefinitionsCalls.json",
    "assignmentsPreview": "specification/authorization/resource-manager/Microsoft.Authorization/"
    "Authorization/preview/2020-08-01-preview/authorization-RoleAssignmentsCalls.json",
    "definitionsPreview": "specification/authorization/resource-manager/Microsoft.Authorization/"
    "Authorization/preview/2022-05-01-preview/authorization-RoleDefinitionsCalls.json",
    "activity": "specification/monitor/resource-manager/Microsoft.Insights/Insights/"
    "stable/2015-04-01/activityLogs_API.json",
}
SOURCES.update(
    {
        "compute": "specification/compute/resource-manager/Microsoft.Compute/"
        "Compute/stable/2025-04-01/ComputeRP.json",
        "networkInterface": "specification/network/resource-manager/Microsoft.Network/"
        "Network/stable/2025-03-01/networkInterface.json",
        "networkPublicIP": "specification/network/resource-manager/Microsoft.Network/"
        "Network/stable/2025-03-01/publicIpAddress.json",
        "networkSecurity": "specification/network/resource-manager/Microsoft.Network/"
        "Network/stable/2025-03-01/networkSecurityGroup.json",
        "networkVirtual": "specification/network/resource-manager/Microsoft.Network/"
        "Network/stable/2025-03-01/virtualNetwork.json",
        "storage": "specification/storage/resource-manager/Microsoft.Storage/"
        "Storage/stable/2025-06-01/openapi.json",
        "vaults": "specification/keyvault/resource-manager/Microsoft.KeyVault/"
        "KeyVault/stable/2025-05-01/openapi.json",
        "workspaces": "specification/operationalinsights/resource-manager/"
        "Microsoft.OperationalInsights/OperationalInsights/stable/2025-02-01/Workspaces.json",
    }
)


def references(value):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for key, item in value.items():
            if key != "x-ms-examples":
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def acquire():
    records, pending = (
        {},
        list(SOURCES.values())
        + ["LICENSE"]
        + [
            posixpath.dirname(SOURCES["activity"]) + "/examples/" + name
            for name in ("GetActivityLogsFiltered.json", "GetActivityLogsFilteredAndSelected.json")
        ],
    )
    while pending:
        path = pending.pop(0)
        if path in records:
            continue
        if path.startswith("/") or ".." in path.split("/") or ":" in path:
            raise ValueError("Source reference escaped the pinned repository")
        response = urllib.request.urlopen(BASE + path, timeout=60)
        # Do not accept a redirect to arbitrary content or a mutable revision.
        if response.url != BASE + path:
            raise ValueError("Unexpected schema redirect")
        raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("Oversized schema")
        local = DATA / "schemas" / path
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(raw)
        records[path] = {
            "path": "schemas/" + path,
            "url": BASE + path,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        if path.endswith(".json"):
            for ref in references(json.loads(raw)):
                file = ref.split("#")[0]
                if file:
                    pending.append(
                        posixpath.normpath(posixpath.join(posixpath.dirname(path), file))
                    )
    manifest = {
        "repository": "Azure/azure-rest-api-specs",
        "revision": REVISION,
        "roots": SOURCES,
        "files": records,
    }
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "references.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    build_metadata(manifest)
    print(f"Pinned {len(records)} official files at {REVISION}")


def build_metadata(manifest):
    data = DATA
    roles = {}
    selected = {
        "Contributor",
        "Key Vault Administrator",
        "Key Vault Contributor",
        "Key Vault Reader",
        "Key Vault Secrets Officer",
        "Key Vault Secrets User",
        "Owner",
        "Reader",
        "Storage Blob Data Contributor",
        "Storage Blob Data Owner",
        "Storage Blob Data Reader",
        "Storage File Data Privileged Contributor",
        "Storage File Data Privileged Reader",
        "Storage Queue Data Contributor",
        "Storage Queue Data Message Processor",
        "Storage Queue Data Message Sender",
        "Storage Queue Data Reader",
        "Storage Table Data Contributor",
        "Storage Table Data Reader",
        "User Access Administrator",
    }
    for category in ("general", "privileged", "storage", "security"):
        url = (
            "https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/"
            + category
        )
        path = data / "role-sources" / (category + ".html")
        raw = path.read_bytes() if path.exists() else urllib.request.urlopen(url, timeout=60).read()
        expected = {
            "general": "a33c4ebb5d465052533744e7c96bfb36f15a6b3cf984c443882d9ee8e66ae8b8",
            "privileged": "a94e2c193da1f213942f767dc306d6c721c9327e21c96acbd54c8693f0700c83",
            "storage": "efef7da2738fc1c4ecc02e6fe99e9894875833c2a6a03d3f68b74ecc3d81e751",
            "security": "17a0abfc394a8ac3d6742c86b04c056ace974fef17e30c1b8ab30a92b8d839e8",
        }[category]
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("Published role source changed; explicit reference update required")
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        manifest["files"][url] = {
            "path": path.relative_to(data).as_posix(),
            "url": url,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        if category in {"storage", "security"}:
            manifest["files"][url].update(
                revision="1b4e136a1da731e1ed16444eb6ce5701bcd8c0f4",
                license="CC-BY-4.0",
            )
        for code in re.findall(
            r'<code[^>]*class="lang-json"[^>]*>(.*?)</code>', raw.decode(), re.S
        ):
            value = json.loads(html.unescape(code))
            name = value.get("roleName")
            if name in selected:
                assert len(value["permissions"]) == 1
                permission = value["permissions"][0]
                assert not permission.get("condition"), (
                    "Conditional roles require a separate evaluator"
                )
                roles[name] = {
                    "id": value["name"],
                    "description": value["description"],
                    "actions": permission["actions"],
                    "notActions": permission["notActions"],
                    "dataActions": permission["dataActions"],
                    "notDataActions": permission["notDataActions"],
                    "assignableScopes": value["assignableScopes"],
                    "source": url,
                }
    assert set(roles) == selected
    (data / "roles.json").write_text(
        json.dumps({"roles": roles}, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    mapping = {
        "subscriptions": {
            "subscriptions.list": "Subscriptions_List",
            "subscriptions.get": "Subscriptions_Get",
            "tenants.list": "Tenants_List",
        },
        "resources": {
            "providers.list": "Providers_List",
            "providers.get": "Providers_Get",
            "providers.register": "Providers_Register",
            "providers.unregister": "Providers_Unregister",
            "groups.list": "ResourceGroups_List",
            "groups.get": "ResourceGroups_Get",
            "groups.head": "ResourceGroups_CheckExistence",
            "groups.put": "ResourceGroups_CreateOrUpdate",
            "groups.patch": "ResourceGroups_Update",
            "groups.delete": "ResourceGroups_Delete",
            "resources.list": "Resources_List",
            "resources.group.list": "Resources_ListByResourceGroup",
            "tags.get": "Tags_GetAtScope",
            "tags.put": "Tags_CreateOrUpdateAtScope",
            "tags.patch": "Tags_UpdateAtScope",
            "tags.delete": "Tags_DeleteAtScope",
        },
        "assignments": {
            "assignments.list": "RoleAssignments_ListForScope",
            "assignments.get": "RoleAssignments_Get",
            "assignments.put": "RoleAssignments_Create",
            "assignments.delete": "RoleAssignments_Delete",
        },
        "definitions": {
            "definitions.list": "RoleDefinitions_List",
            "definitions.get": "RoleDefinitions_Get",
        },
        "activity": {"activity.list": "ActivityLogs_List"},
    }
    operations = {}
    for alias, names in mapping.items():
        source = manifest["roots"][alias]
        doc = json.loads((data / manifest["files"][source]["path"]).read_text(encoding="utf-8"))
        indexed = {
            op["operationId"]: (method.upper(), path, op)
            for path, verbs in doc["paths"].items()
            for method, op in verbs.items()
            if isinstance(op, dict) and "operationId" in op
        }
        for label, identity in names.items():
            method, path, op = indexed[identity]
            schemas = [
                response.get("schema", {}).get("$ref", "").split("/")[-1]
                for code, response in op["responses"].items()
                if code in ("200", "201")
            ]
            operations[label] = {
                "method": method,
                "path": path,
                "operationId": identity,
                "source": source,
                "versions": ["2020-01-01", "2021-01-01"]
                if alias == "subscriptions"
                else [doc["info"]["version"]],
                "schema": next((s for s in schemas if s), None),
                "responses": sorted(op["responses"]),
            }
    # Original Az.Resources7.8 tag cmdlets use this later official version.
    source = manifest["roots"]["resources2024"]
    newer = json.loads((data / manifest["files"][source]["path"]).read_text(encoding="utf-8"))
    older = json.loads(
        (data / manifest["files"][manifest["roots"]["resources"]]["path"]).read_text(
            encoding="utf-8"
        )
    )
    for name in ("TagsResource", "TagsPatchResource", "Tags"):
        assert older["definitions"][name] == newer["definitions"][name]
    for label, contract in operations.items():
        if label.startswith("tags."):
            op = newer["paths"][contract["path"]][contract["method"].lower()]
            assert op["operationId"] == contract["operationId"]
            contract["versions"].append("2024-07-01")
            contract["version_sources"] = {"2021-04-01": contract["source"], "2024-07-01": source}
    source = manifest["roots"]["definitionsPreview"]
    preview = json.loads((data / manifest["files"][source]["path"]).read_text(encoding="utf-8"))
    for label, contract in operations.items():
        if label.startswith("definitions."):
            op = preview["paths"][contract["path"]][contract["method"].lower()]
            assert op["operationId"] == contract["operationId"]
            contract["versions"].append("2022-05-01-preview")
            contract["version_sources"] = {
                "2022-04-01": contract["source"],
                "2022-05-01-preview": source,
            }
    source = manifest["roots"]["assignmentsPreview"]
    preview = json.loads((data / manifest["files"][source]["path"]).read_text(encoding="utf-8"))
    for label, contract in operations.items():
        if label.startswith("assignments."):
            op = preview["paths"][contract["path"]][contract["method"].lower()]
            assert op["operationId"] == contract["operationId"]
            contract["versions"].append("2020-08-01-preview")
            contract["version_sources"] = {
                "2022-04-01": contract["source"],
                "2020-08-01-preview": source,
            }
    (data / "operations.json").write_text(
        json.dumps(operations, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    for filename in ("roles.json", "operations.json"):
        manifest["files"]["derived:" + filename] = {
            "path": filename,
            "url": "derived from pinned sources",
            "sha256": hashlib.sha256((data / filename).read_bytes()).hexdigest(),
        }
    (data / "references.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print("Pinned", len(roles), "official roles and", len(operations), "operation contracts")


if __name__ == "__main__":
    acquire()

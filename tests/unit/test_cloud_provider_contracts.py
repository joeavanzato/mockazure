"""Provider metadata and permissions remain bound to official cached source."""

import html
import json
import re

from mockazure.cloud.contracts import load_contract, verify_references
from mockazure.providers.arm.contracts import DATA, builtin_roles, document, manifest
from mockazure.providers.arm.provider_registry import provider_profiles


def test_provider_metadata_versions_have_actual_resource_paths():
    sources = manifest()["roots"]
    for namespace, profile in provider_profiles().items():
        for resource in profile["resourceTypes"]:
            for version in resource["apiVersions"]:
                namespace_source = resource.get("schemaNamespaces", {}).get(
                    version, resource["schemaNamespace"]
                )
                source = sources[namespace_source]
                schema = document(source)
                assert version == schema["info"]["version"]
                parts = resource["resourceType"].split("/")
                pattern = (
                    "/providers/"
                    + re.escape(namespace)
                    + "/"
                    + "/".join(re.escape(part) + r"/\{[^}]+\}" for part in parts)
                )
                assert any(re.search(pattern + "$", path, re.I) for path in schema["paths"]), (
                    namespace,
                    resource["resourceType"],
                    version,
                )


def test_provider_catalog_sources_and_mirror():
    from pathlib import Path

    verify_references("arm-providers")
    for name in ["Providers_List", "Providers_Get", "Providers_Register", "Providers_Unregister"]:
        contract = load_contract("arm-providers", name)
        assert contract["version"] == "2021-04-01"
        source = next(
            row for row in contract["sources"] if row["localPath"].endswith("/resources.json")
        )
        schema = json.loads(
            (Path("src/mockazure/cloud/data/arm-providers") / source["localPath"]).read_text(
                encoding="utf-8"
            )
        )
        operation = schema["paths"][contract["path"]][contract["method"].lower()]
        assert operation["operationId"] == name
        assert operation["responses"]["200"]["schema"] == contract["responseSchemas"]["200"]
    assert json.loads(
        Path("integrations/contracts/cloud/arm-providers.json").read_text(encoding="utf-8")
    ) == json.loads(
        Path("src/mockazure/cloud/data/arm-providers/operations.json").read_text(encoding="utf-8")
    )


def test_data_roles_preserve_microsoft_permission_definitions():
    roles = builtin_roles()
    observed = set()
    for category in ["storage", "security"]:
        raw = (DATA / "role-sources" / (category + ".html")).read_text(encoding="utf-8")
        for code in re.findall(r'<code[^>]*class="lang-json"[^>]*>(.*?)</code>', raw, re.S):
            source = json.loads(html.unescape(code))
            name = source.get("roleName")
            if name not in roles:
                continue
            observed.add(name)
            assert source["name"] == roles[name]["id"]
            assert len(source["permissions"]) == 1 and not source["permissions"][0].get("condition")
            for field in ["actions", "notActions", "dataActions", "notDataActions"]:
                assert roles[name][field] == source["permissions"][0][field]
    assert len(observed) == 16
    assert roles["Contributor"]["dataActions"] == [] and roles["Owner"]["dataActions"] == []


def test_storage_operations_match_actual_swagger_shapes_and_actions():
    from mockazure.cloud.contracts import _catalog

    contracts = [
        row for row in _catalog("arm-providers")["operations"] if row["version"] == "2025-06-01"
    ]
    assert len(contracts) == 17
    schema = document(manifest()["roots"]["storage"])
    for row in contracts:
        operation = schema["paths"][row["path"]][row["method"].lower()]
        assert operation["operationId"] == row["operationId"]
        for code, response in row["responseSchemas"].items():
            assert operation["responses"][code]["schema"] == response
        if row["operationId"].endswith("_ListKeys"):
            assert row["permissions"] == ["Microsoft.Storage/storageAccounts/listKeys/action"]
        elif row["operationId"].endswith("_RegenerateKey"):
            assert row["permissions"] == ["Microsoft.Storage/storageAccounts/regenerateKey/action"]
    verify_references("arm-providers")


def test_cloud_resource_operation_shapes_are_source_bound():
    from mockazure.cloud.contracts import _catalog
    from mockazure.providers.arm.resource_validation import profiles

    rows = [row for row in _catalog("arm-providers")["operations"] if "resourceType" in row]
    observed = set()
    for row in rows:
        profile = profiles()[row["resourceType"]]
        schema = document(manifest()["roots"][profile["schemaNamespace"]])
        operation = schema["paths"][row["path"]][row["method"].lower()]
        assert operation["operationId"] == row["operationId"]
        assert schema["info"]["version"] == row["version"]
        for status, shape in row["responseSchemas"].items():
            assert operation["responses"][status].get("schema") == shape
        observed.add(row["resourceType"])
    assert observed == set(profiles())
    verify_references("arm-providers")

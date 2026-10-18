import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.filterwarnings("ignore:Duplicate Operation ID:UserWarning")
def test_operation_manifest_matches_real_routes_and_schema(tmp_path):
    from mockazure.config import Settings

    settings = Settings(data_dir=tmp_path, database_url="postgresql+psycopg://localhost/mockazure")
    from mockazure.admin.app import create_app
    from mockazure.providers.graph.schema_reference import schema

    manifest = json.loads((ROOT / "integrations/contracts/coverage-operations.json").read_text())
    app = create_app(settings)
    routes = {
        (method.upper(), path) for path, item in app.openapi()["paths"].items() for method in item
    }
    seen = set()
    for operation in manifest["operations"]:
        key = operation["method"], operation["path"]
        assert key not in seen
        seen.add(key)
        assert key in routes, key
        assert operation["version"] == "v1.0"
        assert operation["sourceUrl"].startswith("https://learn.microsoft.com/")
        assert len(operation["sourceSha256"]) == 64
        assert operation["permissions"] and operation["limits"]
        assert operation["status"] in {"implemented", "qualified"}
        for test in operation["tests"]:
            path, name = test.split("::")
            assert f"def {name}(" in (ROOT / path).read_text()
        for field in ("requestType", "responseType"):
            kind = operation[field]
            if kind:
                assert kind in schema("v1.0")["types"]
    assert len(seen) >= 20


def test_role_extraction_uses_only_action_cells_and_fails_on_missing_role():
    from tools.extract_role_details import extract

    document = b"""<h2 id="reader">Reader</h2><p>microsoft.notAnAction</p>
    <table><tr><td>microsoft.directory/users/read</td><td>Description</td></tr></table>
    <h2 id="writer">Writer</h2><table><td>microsoft.directory/users/write</td></table>"""
    result = extract(document, {"Reader": "id"}, "2026-10-17")
    assert result["roles"]["id"]["rolePermissions"] == [
        {"allowedResourceActions": ["microsoft.directory/users/read"]}
    ]
    with pytest.raises(ValueError, match="Missing"):
        extract(document, {"Absent": "id"}, "2026-10-17")


def test_collector_mapping_is_operation_specific_and_leaves_unsupported_commands_unmapped():
    from tools.collector_coverage import operation_mapping

    directory = operation_mapping("Get-MgDirectoryObject")
    assert directory == [
        {"method": "GET", "path": "/v1.0/directoryObjects/{identifier}", "status": "implemented"}
    ]
    assert operation_mapping("Get-MgDeviceRegisteredUser")[0]["path"].endswith("/registeredUsers")
    assert operation_mapping("Invoke-MgGraphRequest") == []
    from tools.collector_coverage import SURFACES

    mapped = {command for commands in SURFACES.values() for command in commands}
    assert not {"Get-EXOMailboxPermission", "Search-UnifiedAuditLog"} & mapped


def test_query_profiles_have_pinned_sources_and_explicit_local_extensions():
    manifest = json.loads((ROOT / "integrations/contracts/coverage-operations.json").read_text())
    profiles = {row["path"]: row for row in manifest["queryProfiles"]}
    assert len(profiles) >= 10
    for row in profiles.values():
        assert row["method"] == "GET" and row["version"] == "v1.0"
        assert len(row["sourceSha256"]) == 64 and row["sourceUrl"].startswith(
            "https://learn.microsoft.com/"
        )
        assert set(row["supportedOptions"]) <= set(row["documentedOptions"]) | set(
            row["localExtensions"]
        ) | {"$skiptoken"}
    assert "$select" not in profiles["/v1.0/auditLogs/signIns"]["supportedOptions"]
    assert "$select" not in profiles["/v1.0/security/alerts_v2"]["supportedOptions"]
    assert "$orderby" in profiles["/v1.0/auditLogs/signIns"]["localExtensions"]
    assert "$expand" in profiles["/v1.0/groups"]["supportedOptions"]
    assert "ignored" in manifest["advancedQuerySemantics"]["behavior"]


async def test_directory_expansion_capabilities_do_not_promise_nested_paging(tmp_path):
    from types import SimpleNamespace

    from mockazure.admin.routes import capabilities
    from mockazure.config import Settings

    settings = Settings(data_dir=tmp_path, database_url="postgresql://localhost/unit")
    value = await capabilities(
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    )
    assert "graph.directory.members.nested_paging" not in value["provider_operations"]
    manifest = json.loads((ROOT / "integrations/contracts/coverage-operations.json").read_text())
    profile = next(row for row in manifest["queryProfiles"] if row["path"] == "/v1.0/groups")
    assert "without nested nextLink" in profile["notes"]
    assert "dedicated members collection" in profile["notes"]


def test_exchange_collection_contracts_match_proxy_parameters():
    from mockazure.providers.exchange.bootstrap import PARAMETERS
    from tools.collector_coverage import operation_mapping

    manifest = json.loads((ROOT / "integrations/contracts/exchange-mail.json").read_text())
    assert len(manifest["collectionCommands"]) == 6
    for command in manifest["collectionCommands"]:
        assert command["parameters"] == PARAMETERS[command["name"]]
        assert command["sourceUrl"].startswith("https://learn.microsoft.com/")
        assert len(command["sourceSha256"]) == 64
        assert command["responseFields"]
        assert operation_mapping(command["name"]) == [
            {
                "method": "POST",
                "path": "/adminapi/v2.0/{tenant}/InvokeCommand",
                "command": command["name"],
                "status": "implemented",
            }
        ]

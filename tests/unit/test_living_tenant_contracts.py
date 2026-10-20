import hashlib
import json

import pytest

from mockazure.living_tenant.contracts import (
    list_operations,
    load_operation,
    validate_source,
    verify_references,
)


def test_teams_variants_use_their_exact_v1_operation_references():
    assert "team-update-members" in load_operation("teams.team.member.update").source_refs
    assert "chat-get-members" in load_operation("teams.chat.member.get").source_refs
    assert load_operation("teams.chat.member.update").status == "unsupported"
    for action in ("softdelete", "undosoftdelete"):
        assert load_operation(f"teams.chat.message.{action}").path.startswith(
            "/v1.0/users/{user}/chats/"
        )


def test_declared_operation_inventory_contains_all_four_areas():
    operations = list_operations()
    assert {o.area for o in operations} == {"content", "evidence", "identity", "hunting"}
    assert len({o.key for o in operations}) == len(operations)
    hunting = load_operation("hunting.query")
    assert hunting.method == "POST"
    assert hunting.path == "/v1.0/security/runHuntingQuery"
    assert "ThreatHunting.Read.All" in hunting.permissions
    assert hunting.status == "existing"
    assert hunting.role_constraints and hunting.entitlement_constraints
    assert all(o.source_refs for o in operations)
    assert (
        load_operation("identity.sessions.revoke").path == "/v1.0/users/{user}/revokeSignInSessions"
    )
    assert (
        load_operation("cloud.graph-workflows.user.sendMail").path == "/v1.0/users/{user}/sendMail"
    )
    assert load_operation("cloud.exchange.Search-UnifiedAuditLog").supported_variants == [
        "Search-UnifiedAuditLog"
    ]
    with pytest.raises(ValueError, match="Unknown"):
        load_operation("fictional.route")


def test_pinned_references_verify_offline():
    result = verify_references()
    assert result["sources"] > 0
    assert result["operations"] == len(list_operations())


@pytest.mark.parametrize("path", ["../escape", "/absolute", "C:/absolute", "a\\..\\escape"])
def test_source_paths_cannot_escape_package(path):
    with pytest.raises(ValueError):
        validate_source(
            {
                "path": path,
                "url": "https://raw.githubusercontent.com/microsoftgraph/"
                + "microsoft-graph-docs-contrib/"
                + "a" * 40
                + "/api-reference/v1.0/api/user-get.md",
                "revision": "a" * 40,
                "sha256": "b" * 64,
            }
        )


def test_reference_verification_detects_changed_bytes(tmp_path):
    data = b"source"
    (tmp_path / "source.md").write_bytes(data)
    manifest = {
        "sources": {
            "s": {
                "path": "source.md",
                "url": "https://raw.githubusercontent.com/microsoftgraph/"
                + "microsoft-graph-docs-contrib/"
                + "a" * 40
                + "/api-reference/v1.0/api/user-get.md",
                "revision": "a" * 40,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        },
        "operations": [],
    }
    (tmp_path / "operations.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_references(root=tmp_path)["sources"] == 1
    (tmp_path / "source.md").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash"):
        verify_references(root=tmp_path)

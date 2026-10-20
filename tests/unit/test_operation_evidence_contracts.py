from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mockazure.evidence.schemas import SourceEnvelope, SourceRef


def test_operation_manifest_has_explicit_evidence_disposition():
    from mockazure.evidence.operations import mapping, verify_operation_mappings
    from mockazure.living_tenant.contracts import list_operations

    assert verify_operation_mappings() >= 404
    for operation in list_operations():
        contract = mapping(operation.key)
        assert contract.operation_key == operation.key
        assert contract.schema_refs
        assert contract.destinations or contract.limitations


def test_mapping_is_strict_and_unknown_operations_fail_closed():
    from mockazure.evidence.operations import mapping
    from mockazure.evidence.schemas import OperationEvidenceContract

    with pytest.raises(ValueError, match="Unknown"):
        mapping("invented.operation")
    with pytest.raises(ValidationError):
        OperationEvidenceContract(
            operation_key="fake",
            source_kind="workload",
            outcome="success",
            destinations=[],
            schema_refs=[],
            native=True,
            limitations=[],
            surprise=True,
        )


def workload(operation, before, after, **extra):
    now = datetime(2026, 10, 20, tzinfo=UTC)
    return SourceEnvelope(
        ref=SourceRef(tenant_id=uuid4(), kind="workload", id=uuid4()),
        occurred_at=now,
        available_at=now,
        generation=uuid4(),
        source_revision=1,
        facts={
            "operation": operation,
            "scope": "local.test,site,web",
            "actor": {"user": {"id": str(uuid4()), "userPrincipalName": "a@local.test"}},
            "before": before,
            "after": after,
            "provenance": "native",
            "mapping_version": "business-evidence-v1",
            **extra,
        },
    )


@pytest.mark.parametrize(
    ("operation", "before", "after", "expected"),
    [
        ("drive.upload", {}, {"id": "file", "name": "plan.docx"}, "FileUploaded"),
        (
            "drive.upload",
            {"id": "file", "name": "plan.docx"},
            {"id": "file", "size": 10},
            "FileModified",
        ),
        ("drive.update", {"id": "file", "name": "a"}, {"id": "file", "name": "b"}, "FileRenamed"),
        (
            "drive.update",
            {"id": "file", "parentReference": {"id": "a"}},
            {"id": "file", "parentReference": {"id": "b"}},
            "FileMoved",
        ),
        ("drive.copy", {}, {"id": "copy", "name": "plan.docx"}, "FileCopied"),
        ("drive.delete", {"id": "file", "name": "plan.docx"}, {}, "FileDeleted"),
        ("teams.chat.create", {}, {"id": "chat"}, "ChatCreated"),
        (
            "teams.team.member.add",
            {},
            {"id": "member", "userId": "user", "roles": []},
            "MemberAdded",
        ),
        (
            "teams.team.member.update",
            {"id": "member", "roles": []},
            {"id": "member", "roles": ["owner"]},
            "MemberRoleChanged",
        ),
        (
            "teams.channel.message.update",
            {"id": "message"},
            {"id": "message", "deleted": False},
            "GraphMessageUpdated",
        ),
        (
            "teams.chat.message.undosoftdelete",
            {"id": "message"},
            {"id": "message", "deleted": False},
            "GraphMessageUndeleted",
        ),
    ],
)
def test_documented_workload_operations(operation, before, after, expected):
    from mockazure.workloads.facts import project_workload

    source = workload(operation, before, after)
    first, retry = project_workload(source), project_workload(source)
    assert first == retry
    assert len(first.items) == 1
    item = first.items[0]
    assert item.payload["operation"] == expected
    assert item.occurred_at == source.occurred_at
    assert item.payload["userId"] == source.facts["actor"]["user"]["id"]
    assert "mapping_version" not in str(item.payload)
    assert item.payload["auditData"]["dynamicProperties"]["ResultStatus"] == "Succeeded"


@pytest.mark.parametrize(
    "operation",
    [
        "site.permission.create",
        "teams.chat.message.send",
        "teams.channel.message.send",
        "teams.chat.message.softdelete",
        "teams.channel.member.update",
    ],
)
def test_unverified_or_license_specific_workload_events_are_explicitly_suppressed(operation):
    from mockazure.evidence.operations import mapping
    from mockazure.workloads.facts import project_workload

    result = project_workload(workload(operation, {}, {"id": "target"}))
    assert result.receipt_status == "suppressed"
    assert result.items == ()
    assert mapping(operation).limitations


def test_old_workload_facts_are_not_reinterpreted_after_upgrade():
    from mockazure.workloads.facts import project_workload

    source = workload("drive.upload", {}, {"id": "file"})
    del source.facts["mapping_version"]
    assert project_workload(source).items == ()


def test_teams_projection_uses_management_activity_member_role_enum():
    from mockazure.workloads.facts import project_workload

    source = workload(
        "teams.team.member.update",
        {"id": "member", "roles": []},
        {"id": "member", "roles": ["owner"], "email": "owner@local.test"},
    )
    data = project_workload(source).items[0].payload["auditData"]["dynamicProperties"]
    assert data["Members"] == [{"UPN": "owner@local.test", "Role": 1}]
    assert "TeamGuid" not in data  # Opaque fixture scope is not a GUID.
    assert "CommunicationType" not in data


def test_native_projection_routes_to_the_correct_activity_feed():
    from mockazure.activity.contracts import content_type, from_graph
    from mockazure.workloads.facts import project_workload

    for key, target, expected in [
        ("drive.upload", {"id": "file", "name": "plan.docx"}, "Audit.SharePoint"),
        ("teams.chat.create", {"id": "chat"}, "Audit.General"),
    ]:
        source = workload(key, {}, target)
        payload = project_workload(source).items[0].payload
        assert content_type(from_graph(payload, source.ref.tenant_id)) == expected

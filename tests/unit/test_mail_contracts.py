"""Mail contracts use pinned Graph types and operation-specific permissions."""

import importlib.util
import json
from pathlib import Path

import pytest

from mockazure.authorization.catalog import permission
from mockazure.providers.graph.schema_reference import schema, validate_payload
from mockazure.security.catalog import permission_id

ROOT = Path(__file__).resolve().parents[2]


def test_security_alias_is_namespace_local(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "extract_mail_schema", ROOT / "tools/extract_graph_schema.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "aliases.csdl"
    source.write_text(
        """<Edmx xmlns="http://docs.oasis-open.org/odata/ns/edm">
      <Schema Namespace="microsoft.graph" Alias="graph">
        <EntityType Name="entity"><Property Name="id" Type="Edm.String"/></EntityType>
        <EntityType Name="messageRule" BaseType="graph.entity"/>
      </Schema>
      <Schema Namespace="microsoft.graph.security" Alias="self">
        <EnumType Name="state"><Member Name="running"/></EnumType>
        <EntityType Name="auditLogQuery" BaseType="graph.entity">
          <Property Name="status" Type="self.state"/>
        </EntityType>
      </Schema>
      <Schema Namespace="microsoft.graph.other" Alias="self">
        <EnumType Name="state"><Member Name="other"/></EnumType>
        <ComplexType Name="record"><Property Name="status" Type="self.state"/></ComplexType>
      </Schema>
    </Edmx>""",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "ROOTS", ("messageRule", "security.auditLogQuery", "other.record"))
    result = module.extract(source)
    assert result["security.state"]["enum"] == ["running"]
    assert result["other.state"]["enum"] == ["other"]
    assert (
        result["security.auditLogQuery"]["properties"]["status"]["type"]
        == "microsoft.graph.security.state"
    )
    assert result["other.record"]["properties"]["status"]["type"] == "microsoft.graph.other.state"


def test_pinned_mail_schema_and_open_audit_dictionary():
    for version in ("v1.0", "beta"):
        assert "messageRule" in schema(version)["types"]
        validate_payload(
            version,
            "security.auditLogQuery",
            {
                "id": "query",
                "isRecordCountLimitExceeded": False,
                "status": "running",
                "recordTypeFilters": ["ExchangeAdmin"],
            },
        )
        validate_payload(
            version,
            "security.auditLogRecord",
            {
                "id": "record",
                "operation": "New-InboxRule",
                "auditData": {
                    "dynamicProperties": {
                        "Operation": "New-InboxRule",
                        "Parameters": [{"Name": "Name", "Value": "rule"}],
                    }
                },
            },
        )
        with pytest.raises(ValueError):
            validate_payload(version, "security.auditLogRecord", {"id": "record", "madeUpField": 1})
        with pytest.raises(ValueError):
            validate_payload(
                version,
                "security.auditLogQuery",
                {
                    "id": "query",
                    "isRecordCountLimitExceeded": False,
                    "status": "fabricated",
                },
            )
        with pytest.raises(ValueError):
            validate_payload(
                version,
                "security.auditLogRecord",
                {
                    "id": "record",
                    "auditData": {"dynamicProperties": {"bad": float("nan")}},
                },
            )


def test_mail_permission_matrix():
    from mockazure.mail.contracts import operation_requirement

    for kind, read_id, write_id, audit_id in (
        (
            "application",
            "40f97065-369a-49f4-947c-6a255697ae91",
            "6931bccd-447a-43d1-b442-00a195474933",
            "5e1e9171-754d-478c-812c-f1755a9a4c2d",
        ),
        (
            "delegated",
            "87f447af-9fa4-4c32-9dfa-4a57a73d18ce",
            "818c620a-27a9-40bd-a6a5-d96f7d610b4b",
            "1d9e7ac3-0eca-442c-82f9-e92625af6e6d",
        ),
    ):
        assert str(permission_id("MailboxSettings.Read", kind)) == read_id
        assert str(permission_id("MailboxSettings.ReadWrite", kind)) == write_id
        assert str(permission_id("AuditLogsQuery.Read.All", kind)) == audit_id
        assert permission("graph", kind, "MailboxSettings.Read").admin_consent_required == (
            kind == "application"
        )
        read = getattr(operation_requirement("rules.list"), kind + "_permissions")
        assert read == {"MailboxSettings.Read", "MailboxSettings.ReadWrite"}
        write = getattr(operation_requirement("rules.create"), kind + "_permissions")
        assert write == {"MailboxSettings.ReadWrite"}
        audit = getattr(operation_requirement("audit.create"), kind + "_permissions")
        assert "AuditLogsQuery-Exchange.Read.All" in audit
        assert "AuditLog.Read.All" not in audit
        assert "ThreatIntelligence.Read.All" not in audit
    assert operation_requirement("rules.list").access_rule == "mailbox"
    with pytest.raises(ValueError):
        operation_requirement("unknown")


def test_rule_contract_status():
    from mockazure.mail.contracts import validate_mail_payload

    operations = json.loads((ROOT / "src/mockazure/mail/data/operations.json").read_text("utf-8"))
    assert operations["operations"]["rules.create"]["status"] == 201
    assert operations["operations"]["rules.update"]["status"] == 200
    original = {"displayName": "rule", "sequence": 1, "actions": {"markAsRead": True}}
    validated = validate_mail_payload("messageRule", original)
    validated["actions"]["markAsRead"] = False
    assert original["actions"]["markAsRead"] is True
    for field in ("id", "hasError", "isReadOnly"):
        with pytest.raises(ValueError, match="read-only"):
            validate_mail_payload("messageRule", {field: True}, partial=True)
    for body in ({}, {"sequence": True}, {"sequence": -1}, {"actions": {"invented": True}}):
        with pytest.raises(ValueError):
            validate_mail_payload("messageRule", body)
    assert validate_mail_payload("messageRule", {"isEnabled": False}, partial=True) == {
        "isEnabled": False
    }


@pytest.mark.parametrize(
    "bad",
    [
        {"@odata.context": "untrusted"},
        {"x": object()},
        {"x": 2**10000},
        {"x": "x" * 16385},
        {"x": [0] * 10001},
    ],
)
def test_audit_dictionary_is_bounded(bad):
    with pytest.raises(ValueError):
        validate_payload(
            "v1.0",
            "security.auditLogRecord",
            {
                "id": "record",
                "auditData": {"dynamicProperties": bad},
            },
        )


def test_mail_reference_manifest():
    sources = json.loads((ROOT / "src/mockazure/mail/data/references.json").read_text("utf-8"))[
        "sources"
    ]
    assert len(sources) == 34
    assert all(len(row["revision"]) == 40 and len(row["sha256"]) == 64 for row in sources)
    assert any(row["path"].endswith("Get-EXOCasMailbox.md") for row in sources)
    assert any(row["path"].endswith("security-auditlogquery-list-records.md") for row in sources)

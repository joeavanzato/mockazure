"""Cloud command provenance must be bundled, complete and hash-verifiable."""

from importlib import import_module
from pathlib import Path

import pytest


def loader():
    path = Path("src/mockazure/cloud/contracts.py")
    assert path.exists(), "Cloud operation contract loader is not implemented"
    return import_module("mockazure.cloud.contracts")


def test_unified_audit_contract_and_source_closure():
    contracts = loader()
    contracts.verify_references("exchange")
    contract = contracts.load_contract("exchange", "Search-UnifiedAuditLog")
    assert contract["command"] == "Search-UnifiedAuditLog"
    assert contract["limits"]["defaultResultSize"] == 100
    assert contract["limits"]["maxResultSize"] == 5000
    assert contract["sources"]
    assert all(source["sha256"] and source["revision"] for source in contract["sources"])
    with pytest.raises(ValueError):
        contracts.load_contract("exchange", "Invented-Command")


def test_reference_tampering_and_path_escape_fail(tmp_path, monkeypatch):
    contracts = loader()

    original = contracts.DATA
    import shutil

    shutil.copytree(original / "exchange", tmp_path / "exchange")
    monkeypatch.setattr(contracts, "DATA", tmp_path)
    operation = contracts.load_contract("exchange", "Search-UnifiedAuditLog")
    source = operation["sources"][0]
    path = tmp_path / "exchange" / source["localPath"]
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="hash"):
        contracts.verify_references("exchange")
    with pytest.raises(ValueError):
        contracts.load_contract("../exchange", "Search-UnifiedAuditLog")


def test_contract_acquisition_offline_verification():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.acquire_cloud_contracts",
            "--service",
            "exchange",
            "--verify-only",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "exchange: verified" in result.stdout


@pytest.mark.parametrize("value", ["bad\0value", "x" * 321])
def test_audit_scalar_filters_use_the_same_bounds_as_arrays(value):
    from mockazure.providers.exchange.commands import checked_parameters
    from mockazure.providers.graph.errors import GraphError

    for shape in (value, [value]):
        with pytest.raises(GraphError) as error:
            checked_parameters("Search-UnifiedAuditLog", {"UserIds": shape})
        assert error.value.status == 400


def test_recipient_delegation_contracts_have_public_fields_and_current_profile():
    contracts = loader()
    operations = [
        "Get-Recipient",
        "Get-EXORecipient",
        "Get-EXOMailboxPermission",
        "Get-EXORecipientPermission",
        "Add-MailboxPermission",
        "Remove-MailboxPermission",
        "Add-RecipientPermission",
        "Remove-RecipientPermission",
        "Set-Mailbox",
    ]
    for name in operations:
        row = contracts.load_contract("exchange", name)
        assert row["status"] == "implemented-bounded"
        assert row["responseSchemas"].get("fields") or row["responseSchemas"]["type"] == "void"
        assert row["limitations"]
    from tools.verify_cloud_workflows import cases

    definition = next(
        v for v in cases()["A"]["workflows"] if v["id"] == "exchange.recipients-and-delegation"
    )
    assert set(definition["operationIds"]) == set(operations)


def test_security_configuration_contracts_match_dispatch():
    from mockazure.mail.security_configuration_contracts import PARAMETERS
    from mockazure.providers.exchange.bootstrap import PARAMETERS as proxies

    contracts = loader()
    contracts.verify_references("exchange")
    assert len(PARAMETERS) == 26
    for name, parameters in PARAMETERS.items():
        row = contracts.load_contract("exchange", name)
        assert row["parameterSets"][0]["parameters"] == parameters == proxies[name]
        assert row["status"] == "implemented-bounded" and row["limitations"]
    assert set(PARAMETERS["Set-AdminAuditLogConfig"]) == {
        "Force",
        "UnifiedAuditLogIngestionEnabled",
    }

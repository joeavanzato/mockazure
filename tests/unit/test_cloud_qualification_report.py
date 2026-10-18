"""The common client report cannot turn missing/native-client evidence into a pass."""

import copy
from importlib import import_module
from pathlib import Path

import pytest


def validator():
    assert Path("tools/verify_cloud_workflows.py").exists(), "Common workflow runner is missing"
    return import_module("tools.verify_cloud_workflows").validate_report


def sample():
    return {
        "phase": "A",
        "sourceCommit": "a" * 40,
        "sourceFingerprint": "b" * 64,
        "elapsedSeconds": 1.0,
        "ecosystems": {
            key: {"status": "not-applicable", "reason": "Exchange commands require PowerShell"}
            for key in ("python", "dotnet", "typescript")
        }
        | {
            "powershell": {
                "status": "passed",
                "clientVersions": {"ExchangeOnlineManagement": "3.11.0"},
                "cases": [
                    {
                        "workflow": "exchange.unified-audit",
                        "operationIds": ["Search-UnifiedAuditLog"],
                        "transport": "module",
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {
                            "exact_ids": True,
                            "paging": True,
                            "utc_dates": True,
                            "formatted": True,
                        },
                    },
                    {
                        "workflow": "exchange.recipients-and-delegation",
                        "operationIds": [
                            "Get-Recipient",
                            "Get-EXORecipient",
                            "Get-EXOMailboxPermission",
                            "Get-EXORecipientPermission",
                            "Add-MailboxPermission",
                            "Remove-MailboxPermission",
                            "Add-RecipientPermission",
                            "Remove-RecipientPermission",
                            "Set-Mailbox",
                        ],
                        "transport": "module",
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {
                            "identity_selectors": True,
                            "paging": True,
                            "permission_mutations": True,
                            "behalf_mutations": True,
                            "revoked_permissions_absent": True,
                        },
                    },
                ],
            }
        },
    }


def test_qualification_matrix_completeness():
    validate = validator()
    good = sample()
    validate(good, {"python", "powershell", "dotnet", "typescript"})
    missing = copy.deepcopy(good)
    del missing["ecosystems"]["dotnet"]
    with pytest.raises(ValueError):
        validate(missing, {"python", "powershell", "dotnet", "typescript"})
    rest = copy.deepcopy(good)
    rest["ecosystems"]["powershell"]["cases"][0]["transport"] = "rest"
    with pytest.raises(ValueError):
        validate(rest, set(good["ecosystems"]))
    unknown = copy.deepcopy(good)
    unknown["ecosystems"]["powershell"]["cases"][0]["operationIds"] = ["Invented-Command"]
    with pytest.raises(ValueError):
        validate(unknown, set(good["ecosystems"]))
    failed = copy.deepcopy(good)
    failed["ecosystems"]["powershell"]["cases"][0]["assertions"]["paging"] = False
    with pytest.raises(ValueError):
        validate(failed, set(good["ecosystems"]))
    with pytest.raises(ValueError):
        validate(good | {"phase": "all"}, set(good["ecosystems"]))


def test_qualification_rejects_unpinned_client_version():
    validate = validator()
    report = sample()
    report["ecosystems"]["powershell"]["clientVersions"]["ExchangeOnlineManagement"] = "0.0.0"
    with pytest.raises(ValueError):
        validate(report, set(report["ecosystems"]))


def test_qualification_rejects_missing_delegation_workflow():
    report = sample()
    report["ecosystems"]["powershell"]["cases"] = report["ecosystems"]["powershell"]["cases"][:1]
    with pytest.raises(ValueError, match="workflow"):
        validator()(report, {"python", "powershell", "dotnet", "typescript"})

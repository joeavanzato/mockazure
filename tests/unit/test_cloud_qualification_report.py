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
                        "workflow": "exchange.role-management",
                        "operationIds": [
                            "Get-RoleGroup",
                            "Get-RoleGroupMember",
                            "Add-RoleGroupMember",
                            "Remove-RoleGroupMember",
                            "Get-ManagementRoleAssignment",
                            "New-ManagementRoleAssignment",
                            "Remove-ManagementRoleAssignment",
                        ],
                        "transport": "module",
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {
                            "role_groups": True,
                            "membership_roundtrip": True,
                            "assignment_roundtrip": True,
                        },
                    },
                    {
                        "workflow": "exchange.security-configuration",
                        "operationIds": [
                            verb + "-" + kind
                            for kind in (
                                "RemoteDomain",
                                "InboundConnector",
                                "OutboundConnector",
                                "AuthenticationPolicy",
                                "OrganizationRelationship",
                                "SharingPolicy",
                            )
                            for verb in ("Get", "New", "Set", "Remove")
                        ]
                        + ["Get-AdminAuditLogConfig", "Set-AdminAuditLogConfig"],
                        "transport": "module",
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {
                            "six_families_roundtrip": True,
                            "audit_ingestion_toggle": True,
                            "configuration_absence": True,
                        },
                    },
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
    rest["ecosystems"]["powershell"]["cases"][2]["transport"] = "rest"
    with pytest.raises(ValueError):
        validate(rest, set(good["ecosystems"]))
    unknown = copy.deepcopy(good)
    unknown["ecosystems"]["powershell"]["cases"][2]["operationIds"] = ["Invented-Command"]
    with pytest.raises(ValueError):
        validate(unknown, set(good["ecosystems"]))
    failed = copy.deepcopy(good)
    failed["ecosystems"]["powershell"]["cases"][2]["assertions"]["paging"] = False
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
    report["ecosystems"]["powershell"]["cases"] = [
        case
        for case in report["ecosystems"]["powershell"]["cases"]
        if case["workflow"] != "exchange.recipients-and-delegation"
    ]
    with pytest.raises(ValueError, match="workflow"):
        validator()(report, {"python", "powershell", "dotnet", "typescript"})


def test_qualification_rejects_missing_configuration_workflow():
    report = sample()
    report["ecosystems"]["powershell"]["cases"] = [
        r
        for r in report["ecosystems"]["powershell"]["cases"]
        if r["workflow"] != "exchange.security-configuration"
    ]
    with pytest.raises(ValueError, match="workflow"):
        validator()(report, {"python", "powershell", "dotnet", "typescript"})


def test_qualification_rejects_missing_role_workflow():
    report = sample()
    report["ecosystems"]["powershell"]["cases"] = [
        r
        for r in report["ecosystems"]["powershell"]["cases"]
        if r["workflow"] != "exchange.role-management"
    ]
    with pytest.raises(ValueError, match="workflow"):
        validator()(report, {"python", "powershell", "dotnet", "typescript"})


def test_graph_phase_requires_four_native_workflows():
    import json

    from tools.verify_cloud_workflows import cases

    definition = cases()["E"]
    manifest = json.loads(Path("integrations/cloud-workflows-manifest.json").read_text())
    report = {
        "phase": "E",
        "sourceCommit": "a" * 40,
        "sourceFingerprint": "b" * 64,
        "ecosystems": {},
    }
    for ecosystem in ("python", "powershell", "dotnet", "typescript"):
        report["ecosystems"][ecosystem] = {
            "status": "passed",
            "clientVersions": manifest["phaseClients"]["E"][ecosystem],
            "cases": [
                {
                    "workflow": row["id"],
                    "operationIds": row["operationIds"],
                    "transport": row.get("transportByEcosystem", {}).get(
                        ecosystem, row["transport"]
                    ),
                    "expectedStatus": "completed",
                    "actualStatus": "completed",
                    "assertions": {key: True for key in row["assertions"]},
                }
                for row in definition["workflows"]
            ],
        }
    validator()(report, set(report["ecosystems"]))
    broken = copy.deepcopy(report)
    broken["ecosystems"]["dotnet"]["cases"][0]["transport"] = "rest"
    with pytest.raises(ValueError):
        validator()(broken, set(report["ecosystems"]))
    del report["ecosystems"]["typescript"]
    with pytest.raises(ValueError):
        validator()(report, {"python", "powershell", "dotnet", "typescript"})

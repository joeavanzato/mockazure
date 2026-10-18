"""Inventory pinned original PowerShell collectors without executing their code."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "hawk": (
        "a44536f2b4225dd8ea2e463db88d5db40cfac8c7",
        "hawk/hawk-a44536f2b4225dd8ea2e463db88d5db40cfac8c7/Hawk",
    ),
    "mes": (
        "47565f7842784cd3667ff630e3dccb49fa3408ed",
        "Microsoft-Extractor-Suite/Microsoft-Extractor-Suite-47565f7842784cd3667ff630e3dccb49fa3408ed",
    ),
}
# Exact named operations only: a supported prefix does not qualify every cmdlet in a module.
SURFACES = {
    "graph.security.collections": [
        "Get-MgSecurityAlert",
        "Get-MgSecurityAlertV2",
        "Get-MgSecurityIncident",
        "Get-MgSecuritySecureScore",
        "Get-MgSecuritySecureScoreControlProfile",
    ],
    "graph.directory": [
        "Get-MgUser",
        "Get-MgGroup",
        "Get-MgGroupMember",
        "Get-MgGroupMemberOf",
        "Get-MgGroupTransitiveMemberOf",
        "Get-MgDirectoryObjectById",
        "Get-MgUserMemberOf",
        "Get-MgUserTransitiveMemberOf",
        "Get-MgGroupTransitiveMember",
        "Get-MgUserOwnedObject",
        "Get-MgOrganization",
        "Get-MgDirectoryObject",
        "Get-MgSubscribedSku",
        "Get-MgUserLicenseDetail",
    ],
    "graph.audit-risk": [
        "Get-MgAuditLogSignIn",
        "Get-MgAuditLogDirectoryAudit",
        "Get-MgIdentityProtectionRiskyUser",
        "Get-MgIdentityProtectionRiskDetection",
        "Get-MgReportAuthenticationMethodUserRegistrationDetail",
    ],
    "graph.identity-policy": [
        "Get-MgApplication",
        "Get-MgApplicationOwner",
        "Get-MgServicePrincipal",
        "Get-MgServicePrincipalOwner",
        "Get-MgServicePrincipalAppRoleAssignment",
        "Get-MgOauth2PermissionGrant",
        "Get-MgDevice",
        "Get-MgDeviceRegisteredOwner",
        "Get-MgDeviceRegisteredUser",
        "Get-MgUserAppRoleAssignment",
        "Get-MgGroupAppRoleAssignment",
        "Get-MgPolicyConditionalAccessPolicy",
        "Get-MgIdentityConditionalAccessPolicy",
        "Get-MgIdentityConditionalAccessNamedLocation",
        "Get-MgUserAuthenticationMethod",
        "Get-MgRoleManagementDirectoryRoleAssignment",
        "Get-MgRoleManagementDirectoryRoleDefinition",
        "Get-MgDirectoryRole",
        "Get-MgDirectoryRoleMember",
    ],
    "graph.mail-rules": ["Get-MgUserMailFolderMessageRule"],
    "exchange.mail": [
        "Get-OrganizationConfig",
        "Get-MailboxPermission",
        "Get-RecipientPermission",
        "Get-TransportRule",
        "Get-TransportConfig",
        "Get-AcceptedDomain",
        "Get-EXOMailbox",
        "Get-Mailbox",
        "Get-CASMailbox",
        "Get-MailboxStatistics",
        "Get-EXOMailboxStatistics",
        "Get-InboxRule",
        "Get-SweepRule",
        "Get-MessageTraceV2",
        "Get-MessageTraceDetailV2",
    ],
    "arm.inventory-activity": [
        "Get-AzSubscription",
        "Get-AzResource",
        "Get-AzResourceGroup",
        "Get-AzRoleAssignment",
        "Get-AzRoleDefinition",
        "Get-AzActivityLog",
    ],
}


def operation_mapping(command):
    manifest = json.loads((ROOT / "integrations/contracts/coverage-operations.json").read_text())
    result = [
        {key: operation[key] for key in ("method", "path", "status")}
        for operation in manifest["operations"]
        if command.lower() in {name.lower() for name in operation.get("collectorCommands", [])}
    ]

    exchange = json.loads((ROOT / "integrations/contracts/exchange-mail.json").read_text())
    for operation in exchange.get("collectionCommands", []):
        if command.lower() == operation["name"].lower():
            result.append(
                {
                    "method": operation["method"],
                    "path": operation["path"],
                    "command": operation["name"],
                    "status": operation["status"],
                }
            )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vendor", type=Path, default=ROOT / "work/vendor/collectors")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "integrations/contracts/collection-coverage.json"
    )
    args = parser.parse_args()
    lookup = {c.lower(): surface for surface, calls in SURFACES.items() for c in calls}
    inventory = {
        "scope": "Static original-source inventory, not complete-workflow certification",
        "mapping": (
            "Named calls map to implemented bounded surface families; "
            "parameters and dynamic URLs still require qualification."
        ),
        "collectors": {},
        "next_gaps": [
            "Exchange RemoteDomain/AdminAuditLogConfig and compiled permission collector calls",
            "Mailbox messages and attachments",
            "SharePoint/OneDrive evidence and audit collectors",
            "Provider-specific Azure diagnostics and storage logs",
            "Defender hunting and Purview operations beyond existing UAL reads",
        ],
    }
    scratch = ROOT / "work/defaults-coverage"
    scratch.mkdir(parents=True, exist_ok=True)
    for name, (revision, relative) in SOURCES.items():
        directory = args.vendor / relative
        if not directory.is_dir():
            raise RuntimeError("Download the pinned collector archives first")
        output = scratch / (name + "-inventory.json")
        subprocess.run(
            [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-File",
                str(ROOT / "integrations/powershell/Get-CollectorInventory.ps1"),
                "-SourceRoot",
                str(directory),
                "-OutputPath",
                str(output),
            ],
            check=True,
            timeout=120,
        )
        result = json.loads(output.read_text(encoding="utf-8-sig"))
        helpers = {
            name.lower() for file in result["files"] for name in file["function_definitions"]
        }
        for file in result["files"]:
            assert (
                hashlib.sha256((directory / file["path"]).read_bytes()).hexdigest()
                == file["sha256"]
            )
            for call in file["calls"]:
                name_key = call["command"].lower()
                if name_key in helpers or name_key in {
                    "get-mgcontext",
                    "get-azcontext",
                    "get-azaccesstoken",
                }:
                    call.update(surface=None, status="local-helper-or-auth-context")
                    continue
                surface = lookup.get(name_key)
                call["surface"] = surface
                operations = operation_mapping(call["command"])
                if operations:
                    call["operations"] = operations
                call["status"] = (
                    "implemented-profile-needs-parameter-check"
                    if surface
                    else "unmapped-review-required"
                )
        result["files"].sort(key=lambda x: x["path"])
        # PowerShell hashtable enumeration is process-dependent; stabilize artifact key order.
        files = []
        for file in result["files"]:
            file["calls"] = [
                {
                    key: call[key]
                    for key in ("parameters", "line", "command", "surface", "status", "operations")
                    if key in call
                }
                for call in file["calls"]
            ]
            files.append(
                {
                    key: file[key]
                    for key in (
                        "calls",
                        "path",
                        "dynamic_command_lines",
                        "url_templates",
                        "function_definitions",
                        "sha256",
                    )
                }
            )
        inventory["collectors"][name] = {
            "revision": revision,
            "files": files,
            "method": result["method"],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    calls = [
        c
        for source in inventory["collectors"].values()
        for f in source["files"]
        for c in f["calls"]
    ]
    print(
        json.dumps(
            {
                "files": sum(len(s["files"]) for s in inventory["collectors"].values()),
                "named_calls": len(calls),
                "mapped_calls": sum(c["surface"] is not None for c in calls),
                "unmapped_commands": sorted(
                    {c["command"] for c in calls if c["status"] == "unmapped-review-required"}
                ),
            }
        )
    )


if __name__ == "__main__":
    main()

"""Explicit bounded role-to-command mapping; no OAuth permissions are granted here."""

import json
from pathlib import Path

PARAMETERS = json.loads(
    (Path(__file__).parent / "data/role_management_parameters.json").read_text(encoding="utf-8")
)
RECIPIENT_READS = frozenset(
    {
        "Get-Recipient",
        "Get-EXORecipient",
        "Get-Mailbox",
        "Get-EXOMailbox",
        "Get-EXOMailboxStatistics",
        "Get-EXOMailboxFolderStatistics",
        "Get-EXOCasMailbox",
        "Get-MailboxPermission",
        "Get-RecipientPermission",
        "Get-EXOMailboxPermission",
        "Get-EXORecipientPermission",
    }
)
CONFIGURATION_READS = frozenset(
    {
        "Get-OrganizationConfig",
        "Get-TransportConfig",
        "Get-AcceptedDomain",
        "Get-TransportRule",
        "Get-RemoteDomain",
        "Get-InboundConnector",
        "Get-OutboundConnector",
        "Get-AuthenticationPolicy",
        "Get-AdminAuditLogConfig",
        "Get-OrganizationRelationship",
        "Get-SharingPolicy",
        "Get-RoleGroup",
        "Get-RoleGroupMember",
        "Get-ManagementRoleAssignment",
    }
)
ROLE_COMMANDS = {
    "View-Only Recipients": RECIPIENT_READS,
    "View-Only Configuration": CONFIGURATION_READS,
    "Mail Recipients": RECIPIENT_READS
    | {
        "Set-Mailbox",
        "Add-MailboxPermission",
        "Remove-MailboxPermission",
        "Add-RecipientPermission",
        "Remove-RecipientPermission",
    },
    "Remote and Accepted Domains": frozenset(
        {"Get-AcceptedDomain", *(v + "-RemoteDomain" for v in ("Get", "New", "Set", "Remove"))}
    ),
    "Role Management": frozenset(PARAMETERS) | {"Get-OrganizationConfig"},
}
GROUP_ROLES = {
    "Organization Management": tuple(ROLE_COMMANDS),
    "Recipient Management": ("Mail Recipients",),
    "View-Only Organization Management": ("View-Only Configuration", "View-Only Recipients"),
}

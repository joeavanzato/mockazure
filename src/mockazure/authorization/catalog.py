from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDefinition:
    resource: str
    name: str
    kind: str
    admin_consent_required: bool


CATALOG = (
    PermissionDefinition("graph", "User.Read", "delegated", False),
    *(
        PermissionDefinition("graph", name, kind, kind == "application")
        for name in ("MailboxSettings.Read", "MailboxSettings.ReadWrite")
        for kind in ("application", "delegated")
    ),
    PermissionDefinition("graph", "Directory.AccessAsUser.All", "delegated", True),
    *(
        PermissionDefinition("graph", name, kind, True)
        for name in (
            "SecurityEvents.Read.All",
            "SecurityEvents.ReadWrite.All",
            "SecurityAlert.Read.All",
            "SecurityAlert.ReadWrite.All",
            "SecurityIncident.Read.All",
            "SecurityIncident.ReadWrite.All",
            "AuditLog.Read.All",
            "AuditLogsQuery.Read.All",
            "AuditLogsQuery-CRM.Read.All",
            "AuditLogsQuery-Endpoint.Read.All",
            "AuditLogsQuery-Entra.Read.All",
            "AuditLogsQuery-Exchange.Read.All",
            "AuditLogsQuery-OneDrive.Read.All",
            "AuditLogsQuery-SharePoint.Read.All",
            "IdentityRiskyUser.Read.All",
            "IdentityRiskyUser.ReadWrite.All",
            "IdentityRiskEvent.Read.All",
            "Organization.Read.All",
            "Organization.ReadWrite.All",
            "LicenseAssignment.Read.All",
            "LicenseAssignment.ReadWrite.All",
            "Directory.Read.All",
            "Directory.ReadWrite.All",
            "User.Read.All",
            "User.ReadWrite.All",
            "User.ReadUpdate.All",
            "User.Create",
            "User.EnableDisableAccount.All",
            "GroupMember.Read.All",
            "GroupMember.ReadBasic.All",
            "GroupMember.ReadWrite.All",
            "Group.ReadBasic.All",
            "Group.Read.All",
            "Group.ReadWrite.All",
            "Member.Read.Hidden",
            "Application.Read.All",
            "Application.ReadWrite.All",
            "AppRoleAssignment.ReadWrite.All",
            "DelegatedPermissionGrant.ReadWrite.All",
            "Device.Read.All",
            "RoleManagement.Read.Directory",
            "RoleManagement.ReadWrite.Directory",
            "UserAuthenticationMethod.Read.All",
            "UserAuthenticationMethod.ReadWrite.All",
            "Policy.Read.All",
            "Policy.ReadWrite.ConditionalAccess",
        )
        for kind in ("application", "delegated")
    ),
    *(
        PermissionDefinition("graph", name, "delegated", True)
        for name in ("UserAuthenticationMethod.Read", "UserAuthenticationMethod.ReadWrite")
    ),
    *(
        PermissionDefinition("graph", f"UserAuthMethod-{family}.{suffix}", kind, True)
        for family in ("Password", "Phone", "Email", "MicrosoftAuthApp", "Passkey")
        for suffix in ("Read", "ReadWrite", "Read.All", "ReadWrite.All")
        for kind in (("application", "delegated") if suffix.endswith(".All") else ("delegated",))
    ),
    PermissionDefinition("graph", "Policy.Read.ConditionalAccess", "application", True),
    PermissionDefinition("graph", "Policy.Read.ConditionalAccess", "delegated", False),
    PermissionDefinition("graph", "User.ReadBasic.All", "delegated", False),
    PermissionDefinition("graph", "User.ReadWrite", "delegated", False),
    PermissionDefinition("graph", "Group.Create", "application", True),
    PermissionDefinition("graph", "Application.ReadWrite.OwnedBy", "application", True),
    PermissionDefinition("graph", "Device.ReadWrite.All", "application", True),
    PermissionDefinition("exchange", "Exchange.ManageAsApp", "application", True),
    PermissionDefinition("exchange", "Exchange.Manage", "delegated", True),
    PermissionDefinition("arm", "user_impersonation", "delegated", True),
)
DIRECTORY_ROLES = frozenset({"Global Administrator", "Global Reader", "Directory Readers"})


def permission(resource: str, kind: str, name: str) -> PermissionDefinition:
    for entry in CATALOG:
        if (entry.resource, entry.kind, entry.name) == (resource, kind, name):
            return entry
    raise ValueError("Unsupported permission for resource and permission kind")


GRAPH_LICENSING_ROLES = frozenset(
    {
        "Guest Inviter",
        "License Administrator",
        "Dynamics 365 Business Central Administrator",
    }
)

GRAPH_EVIDENCE_ROLES = frozenset(
    {"Reports Reader", "Security Reader", "Security Operator", "Security Administrator"}
)

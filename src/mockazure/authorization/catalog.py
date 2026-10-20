from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDefinition:
    resource: str
    name: str
    kind: str
    admin_consent_required: bool


CATALOG = (
    PermissionDefinition("graph", "User.DeleteRestore.All", "application", True),
    PermissionDefinition("graph", "User.DeleteRestore.All", "delegated", True),
    PermissionDefinition("graph", "User.RevokeSessions.All", "application", True),
    PermissionDefinition("graph", "User.RevokeSessions.All", "delegated", True),
    PermissionDefinition("graph", "Chat.Create", "application", True),
    PermissionDefinition("graph", "Chat.Create", "delegated", False),
    PermissionDefinition("graph", "ChatMessage.Send", "delegated", False),
    PermissionDefinition("graph", "ChannelMessage.Send", "delegated", False),
    PermissionDefinition("graph", "ChannelMessage.ReadWrite", "delegated", True),
    PermissionDefinition("graph", "TeamMember.ReadWriteNonOwnerRole.All", "application", True),
    PermissionDefinition("graph", "TeamMember.ReadWriteNonOwnerRole.All", "delegated", True),
    *(
        PermissionDefinition("graph", name, kind, kind == "application")
        for name in ("Calendars.Read", "Calendars.ReadWrite")
        for kind in ("application", "delegated")
    ),
    PermissionDefinition("graph", "Calendars.ReadBasic.All", "application", True),
    *(
        PermissionDefinition("graph", name, "delegated", False)
        for name in ("Calendars.ReadBasic", "Calendars.Read.Shared", "Calendars.ReadWrite.Shared")
    ),
    PermissionDefinition("graph", "Policy.Read.AuthenticationMethod", "application", True),
    PermissionDefinition("graph", "Policy.Read.AuthenticationMethod", "delegated", True),
    PermissionDefinition("graph", "Policy.ReadWrite.AuthenticationMethod", "application", True),
    PermissionDefinition("graph", "Policy.ReadWrite.AuthenticationMethod", "delegated", True),
    PermissionDefinition("activity", "ActivityFeed.Read", "application", True),
    PermissionDefinition("activity", "ActivityFeed.ReadDlp", "application", True),
    PermissionDefinition("graph", "DeviceManagementManagedDevices.Read.All", "application", True),
    PermissionDefinition("graph", "DeviceManagementManagedDevices.Read.All", "delegated", True),
    PermissionDefinition(
        "graph", "DeviceManagementManagedDevices.ReadWrite.All", "application", True
    ),
    PermissionDefinition(
        "graph", "DeviceManagementManagedDevices.ReadWrite.All", "delegated", True
    ),
    PermissionDefinition("graph", "DeviceManagementConfiguration.Read.All", "application", True),
    PermissionDefinition("graph", "DeviceManagementConfiguration.Read.All", "delegated", True),
    PermissionDefinition(
        "graph", "DeviceManagementConfiguration.ReadWrite.All", "application", True
    ),
    PermissionDefinition("graph", "DeviceManagementConfiguration.ReadWrite.All", "delegated", True),
    PermissionDefinition("graph", "Channel.ReadBasic.All", "application", True),
    PermissionDefinition("graph", "Channel.ReadBasic.All", "delegated", False),
    PermissionDefinition("graph", "ChannelMember.Read.All", "application", True),
    PermissionDefinition("graph", "ChannelMember.Read.All", "delegated", True),
    PermissionDefinition("graph", "ChannelMember.ReadWrite.All", "application", True),
    PermissionDefinition("graph", "ChannelMember.ReadWrite.All", "delegated", True),
    PermissionDefinition("graph", "ChannelMessage.Read.All", "application", True),
    PermissionDefinition("graph", "ChannelMessage.Read.All", "delegated", True),
    PermissionDefinition("graph", "ChannelSettings.Read.All", "application", True),
    PermissionDefinition("graph", "ChannelSettings.Read.All", "delegated", True),
    PermissionDefinition("graph", "ChannelSettings.ReadWrite.All", "application", True),
    PermissionDefinition("graph", "ChannelSettings.ReadWrite.All", "delegated", True),
    PermissionDefinition("graph", "Chat.Read", "delegated", False),
    PermissionDefinition("graph", "Chat.Read.All", "application", True),
    PermissionDefinition("graph", "Chat.ReadBasic", "delegated", False),
    PermissionDefinition("graph", "Chat.ReadBasic.All", "application", True),
    PermissionDefinition("graph", "Chat.ReadWrite", "delegated", False),
    PermissionDefinition("graph", "Chat.ReadWrite.All", "application", True),
    PermissionDefinition("graph", "ChatMember.Read", "delegated", True),
    PermissionDefinition("graph", "ChatMember.Read.All", "application", True),
    PermissionDefinition("graph", "ChatMember.ReadWrite", "delegated", True),
    PermissionDefinition("graph", "ChatMember.ReadWrite.All", "application", True),
    PermissionDefinition("graph", "Team.ReadBasic.All", "application", True),
    PermissionDefinition("graph", "Team.ReadBasic.All", "delegated", False),
    PermissionDefinition("graph", "TeamMember.Read.All", "application", True),
    PermissionDefinition("graph", "TeamMember.Read.All", "delegated", True),
    PermissionDefinition("graph", "TeamMember.ReadWrite.All", "application", True),
    PermissionDefinition("graph", "TeamMember.ReadWrite.All", "delegated", True),
    PermissionDefinition("graph", "TeamSettings.Read.All", "application", True),
    PermissionDefinition("graph", "TeamSettings.Read.All", "delegated", True),
    PermissionDefinition("graph", "TeamSettings.ReadWrite.All", "application", True),
    PermissionDefinition("graph", "TeamSettings.ReadWrite.All", "delegated", True),
    PermissionDefinition("graph", "Files.Read", "delegated", False),
    PermissionDefinition("graph", "Files.Read.All", "application", True),
    PermissionDefinition("graph", "Files.Read.All", "delegated", False),
    PermissionDefinition("graph", "Files.ReadWrite", "delegated", False),
    PermissionDefinition("graph", "Files.ReadWrite.All", "application", True),
    PermissionDefinition("graph", "Files.ReadWrite.All", "delegated", False),
    PermissionDefinition("graph", "Sites.FullControl.All", "application", True),
    PermissionDefinition("graph", "Sites.FullControl.All", "delegated", True),
    PermissionDefinition("graph", "Sites.Manage.All", "application", True),
    PermissionDefinition("graph", "Sites.Manage.All", "delegated", False),
    PermissionDefinition("graph", "Sites.Read.All", "application", True),
    PermissionDefinition("graph", "Sites.Read.All", "delegated", False),
    PermissionDefinition("graph", "Sites.ReadWrite.All", "application", True),
    PermissionDefinition("graph", "Sites.ReadWrite.All", "delegated", False),
    PermissionDefinition("graph", "Sites.Selected", "application", True),
    PermissionDefinition("graph", "Sites.Selected", "delegated", False),
    PermissionDefinition("graph", "User.Read", "delegated", False),
    *(
        PermissionDefinition("graph", name, kind, kind == "application")
        for name in ("MailboxSettings.Read", "MailboxSettings.ReadWrite")
        for kind in ("application", "delegated")
    ),
    *(
        PermissionDefinition("graph", name, kind, kind == "application")
        for name in ("Mail.Read", "Mail.ReadWrite")
        for kind in ("application", "delegated")
    ),
    *(
        PermissionDefinition("graph", name, "delegated", False)
        for name in (
            "Mail.Read.Shared",
            "Mail.ReadWrite.Shared",
            "Mail.ReadBasic.Shared",
            "Mail.Send.Shared",
        )
    ),
    PermissionDefinition("graph", "Mail.Send", "application", True),
    PermissionDefinition("graph", "Mail.Send", "delegated", False),
    PermissionDefinition("graph", "Mail.ReadBasic", "delegated", False),
    PermissionDefinition("graph", "Mail.ReadBasic.All", "application", True),
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
    PermissionDefinition("logs", "Data.Read", "delegated", True),
    PermissionDefinition("arm", "user_impersonation", "delegated", True),
    PermissionDefinition("storage", "user_impersonation", "delegated", True),
    PermissionDefinition("keyvault", "user_impersonation", "delegated", True),
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

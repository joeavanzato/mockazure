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
        PermissionDefinition("graph", name, kind, True)
        for name in (
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
        )
        for kind in ("application", "delegated")
    ),
    PermissionDefinition("graph", "User.ReadBasic.All", "delegated", False),
    PermissionDefinition("graph", "User.ReadWrite", "delegated", False),
    PermissionDefinition("graph", "Group.Create", "application", True),
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


GRAPH_LICENSING_ROLES = frozenset({
    "Guest Inviter", "License Administrator", "Dynamics 365 Business Central Administrator",
})

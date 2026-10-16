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
        for name in ("Organization.Read.All", "Directory.Read.All")
        for kind in ("application", "delegated")
    ),
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

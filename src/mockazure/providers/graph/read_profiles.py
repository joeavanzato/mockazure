"""Declared shared read-budget buckets; callers cannot create arbitrary SQL scopes."""

from typing import Literal, get_args

ReadResource = Literal[
    "deviceManagement",
    "teams",
    "chats",
    "sites",
    "drives",
    "users",
    "groups",
    "signIn",
    "directoryAudit",
    "riskDetection",
    "riskyUser",
    "applications",
    "servicePrincipals",
    "devices",
    "directoryObjects",
    "organization",
    "subscribedSkus",
    "directoryRoles",
    "roleManagement",
    "oauth2PermissionGrants",
    "conditionalAccess",
    "userRegistrationDetails",
    "security.alerts",
    "security.alerts_v2",
    "security.incidents",
    "security.secureScores",
    "security.secureScoreControlProfiles",
]
READ_RESOURCES = frozenset(get_args(ReadResource))
COLLECTION_ROOTS = {
    name: name
    for name in (
        "deviceManagement",
        "teams",
        "chats",
        "sites",
        "drives",
        "applications",
        "servicePrincipals",
        "devices",
        "directoryObjects",
        "organization",
        "subscribedSkus",
        "directoryRoles",
        "roleManagement",
        "oauth2PermissionGrants",
    )
}


def collection_resource(path):
    parts = path.strip("/").split("/")
    if not parts or parts[0] not in {"v1.0", "beta"} or len(parts) < 2:
        return None
    navigation = (
        parts[2]
        if len(parts) > 2 and parts[1] == "me"
        else (parts[3] if len(parts) > 3 and parts[1] == "users" else None)
    )
    if navigation == "managedDevices":
        return "deviceManagement"
    if navigation in {"chats", "joinedTeams"}:
        return "chats" if navigation == "chats" else "teams"
    root = parts[1].split("(", 1)[0]
    if root in COLLECTION_ROOTS:
        return COLLECTION_ROOTS[root]
    if parts[1:3] == ["identity", "conditionalAccess"]:
        return "conditionalAccess"
    if parts[1:4] == ["reports", "authenticationMethods", "userRegistrationDetails"]:
        return "userRegistrationDetails"
    if root == "security" and len(parts) >= 3:
        name = "security." + parts[2]
        return name if name in READ_RESOURCES else None
    return None

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

DirectoryKind = Literal["user", "group"]

USER_FIELDS = frozenset(
    {
        "id",
        "displayName",
        "userPrincipalName",
        "givenName",
        "surname",
        "mail",
        "mailNickname",
        "accountEnabled",
        "usageLocation",
        "assignedLicenses",
        "assignedPlans",
        "userType",
        "externalUserState",
        "externalUserStateChangeDateTime",
        "createdDateTime",
        "signInSessionsValidFromDateTime",
        "jobTitle",
        "department",
        "officeLocation",
        "mobilePhone",
        "businessPhones",
        "preferredLanguage",
    }
)
GROUP_FIELDS = frozenset(
    {
        "id",
        "displayName",
        "description",
        "mailNickname",
        "mail",
        "mailEnabled",
        "securityEnabled",
        "groupTypes",
        "visibility",
        "createdDateTime",
        "isAssignableToRole",
    }
)
PUBLIC_FIELDS = {"user": USER_FIELDS, "group": GROUP_FIELDS}
FACT_FIELDS = frozenset(
    {
        "schema_version",
        "operation",
        "actor_id",
        "client_id",
        "correlation_id",
        "provenance",
        "target_ids",
        "relationships",
        "initiated_by",
        "targets",
        "object_kind",
        "changed_fields",
        "legacy_incomplete",
        "modified_properties",
    }
)


@dataclass(frozen=True)
class DirectoryObject:
    kind: DirectoryKind
    id: UUID
    tenant_id: UUID
    properties: dict


@dataclass(frozen=True)
class DirectoryChange:
    revision: int
    kind: DirectoryKind
    object_id: UUID
    changed_fields: frozenset[str]
    removed: bool = False

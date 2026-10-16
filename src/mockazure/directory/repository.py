"""Public projections of the identity source of truth."""

from mockazure.directory.models import Group
from mockazure.directory.types import GROUP_FIELDS, USER_FIELDS, DirectoryObject
from mockazure.identity.models import User


def public_object(row: User | Group) -> DirectoryObject:
    if isinstance(row, User):
        properties = dict.fromkeys(USER_FIELDS)
        properties.update({k: v for k, v in row.profile.items() if k in USER_FIELDS})
        properties.update(
            id=str(row.id),
            displayName=row.display_name,
            userPrincipalName=row.upn,
            accountEnabled=row.enabled,
            userType=row.profile.get("userType", "Member"),
            mailNickname=row.profile.get("mailNickname", row.upn.partition("@")[0][:64]),
            businessPhones=row.profile.get("businessPhones", []),
            createdDateTime=row.created_at.isoformat().replace("+00:00", "Z"),
        )
        kind = "user"
    else:
        properties = dict.fromkeys(GROUP_FIELDS)
        properties.update(
            groupTypes=[],
            mailEnabled=False,
            securityEnabled=True,
            isAssignableToRole=False,
        )
        properties.update({k: v for k, v in row.profile.items() if k in GROUP_FIELDS})
        properties.update(
            id=str(row.id),
            displayName=row.display_name,
            mailNickname=row.mail_nickname,
            createdDateTime=row.created_at.isoformat().replace("+00:00", "Z"),
        )
        kind = "group"
    return DirectoryObject(kind, row.id, row.tenant_id, properties)

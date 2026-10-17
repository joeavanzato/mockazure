"""Operation, property and relationship permissions for the declared directory profile."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.directory.models import DirectoryRelation
from mockazure.directory.types import GROUP_FIELDS, USER_FIELDS
from mockazure.identity.models import DirectoryRole, User
from mockazure.identity.types import PrincipalContext
from mockazure.providers.graph.errors import GraphError

USER_READ = frozenset(
    {"User.Read.All", "User.ReadWrite.All", "Directory.Read.All", "Directory.ReadWrite.All"}
)
USER_WRITE = frozenset({"User.ReadUpdate.All", "User.ReadWrite.All", "Directory.ReadWrite.All"})
GROUP_READ = frozenset(
    {
        "GroupMember.Read.All",
        "Group.Read.All",
        "Group.ReadWrite.All",
        "Directory.Read.All",
        "Directory.ReadWrite.All",
    }
)
GROUP_FULL = frozenset(
    {"Group.Read.All", "Group.ReadWrite.All", "Directory.Read.All", "Directory.ReadWrite.All"}
)
MEMBER_READ = GROUP_READ | {
    "GroupMember.ReadBasic.All",
    "GroupMember.ReadWrite.All",
    "Group.ReadBasic.All",
}
MEMBER_WRITE = frozenset(
    {"GroupMember.ReadWrite.All", "Group.ReadWrite.All", "Directory.ReadWrite.All"}
)
USER_BASIC = frozenset({"id", "displayName", "userPrincipalName", "givenName", "surname", "mail"})
GROUP_BASIC = frozenset(
    {"id", "displayName", "mail", "mailNickname", "securityEnabled", "groupTypes"}
)
USER_ADMIN = frozenset({"User Administrator", "Global Administrator"})
GROUP_ADMIN = frozenset(
    {
        "Directory Writers",
        "Groups Administrator",
        "Identity Governance Administrator",
        "User Administrator",
        "Intune Administrator",
        "Global Administrator",
    }
)
GRAPH_DIRECTORY_ROLES = USER_ADMIN | GROUP_ADMIN | {"Privileged Authentication Administrator"}


@dataclass(frozen=True)
class GraphOperation:
    version: str
    resource: str
    method: str
    fields: frozenset[str] = frozenset()


def deny():
    raise GraphError(
        403, "Authorization_RequestDenied", "Insufficient privileges to complete the operation"
    )


def require(permissions: frozenset[str], alternatives: tuple[frozenset[str], ...]) -> None:
    if not any(required <= permissions for required in alternatives):
        deny()


def any_of(names):
    return tuple(frozenset({name}) for name in names)


async def authorize_directory(
    session: AsyncSession,
    context: PrincipalContext,
    operation: GraphOperation,
    target_id: UUID | None,
) -> None:
    from mockazure.authorization.evaluator import AuthorizationDenied, require_current_identity

    try:
        await require_current_identity(context, session)
    except AuthorizationDenied:
        deny()
    if operation.version not in {"v1.0", "beta"}:
        raise GraphError(400, "Request_UnsupportedQuery", "API version is not supported")
    delegated = context.token_kind == "delegated"
    roles = set()
    if delegated:
        user = await session.scalar(
            select(User).where(
                User.tenant_id == context.tenant_id,
                User.id == context.subject_id,
                User.enabled.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if user is None:
            deny()
        roles = set(
            await session.scalars(
                select(DirectoryRole.role).where(
                    DirectoryRole.tenant_id == context.tenant_id,
                    DirectoryRole.user_id == context.subject_id,
                )
            )
        )
        if user.profile.get("userType", "Member") != "Member" and target_id != context.subject_id:
            deny()
    resource, method, permissions = operation.resource, operation.method, context.permissions
    self_access = delegated and target_id is not None and target_id == context.subject_id
    if method == "GET":
        if resource in {"users", "users.delta"}:
            allowed = USER_READ
            if delegated and resource == "users":
                allowed = allowed | {"User.ReadBasic.All"}
                if self_access:
                    allowed = allowed | {"User.Read", "User.ReadWrite"}
        elif resource in {"groups", "groups.delta"}:
            allowed = GROUP_READ
        elif resource in {"members", "transitiveMembers"}:
            allowed = MEMBER_READ
        elif resource == "owners":
            allowed = GROUP_READ | {"GroupMember.ReadWrite.All"}
        elif resource in {"memberOf", "transitiveMemberOf"}:
            allowed = frozenset(
                {
                    "User.Read.All",
                    "Directory.Read.All",
                    "Directory.ReadWrite.All",
                    "Group.Read.All",
                    "GroupMember.Read.All",
                }
            )
            if self_access:
                allowed |= {"User.Read"}
        else:
            raise GraphError(400, "Request_UnsupportedQuery", "Unsupported directory operation")
        require(permissions, any_of(allowed))
        return

    required_roles = frozenset()
    owner_allowed = False
    if resource == "users":
        if method == "POST":
            require(
                permissions,
                any_of({"User.Create", "User.ReadWrite.All", "Directory.ReadWrite.All"}),
            )
            required_roles = USER_ADMIN
        elif method == "DELETE":
            require(permissions, any_of({"User.ReadWrite.All"}))
            required_roles = USER_ADMIN | {"Privileged Authentication Administrator"}
        elif method == "PATCH":
            if operation.fields - {"accountEnabled"}:
                self_fields = {
                    "displayName",
                    "givenName",
                    "surname",
                    "jobTitle",
                    "officeLocation",
                    "preferredLanguage",
                }
                if not (
                    self_access
                    and operation.fields <= self_fields
                    and "User.ReadWrite" in permissions
                ):
                    require(permissions, any_of(USER_WRITE))
                    required_roles = USER_ADMIN
            if "accountEnabled" in operation.fields:
                require(
                    permissions, (frozenset({"User.EnableDisableAccount.All", "User.Read.All"}),)
                )
                required_roles = USER_ADMIN
        else:
            raise GraphError(400, "Request_UnsupportedQuery", "Unsupported directory operation")
    elif resource == "groups":
        if method == "POST":
            allowed = {"Group.ReadWrite.All", "Directory.ReadWrite.All"}
            if not delegated:
                allowed.add("Group.Create")
            require(permissions, any_of(allowed))
        elif method in {"PATCH", "DELETE"}:
            require(permissions, any_of({"Group.ReadWrite.All"}))
            required_roles, owner_allowed = GROUP_ADMIN, True
        else:
            raise GraphError(400, "Request_UnsupportedQuery", "Unsupported directory operation")
    elif resource == "members" and method in {"POST", "DELETE"}:
        require(permissions, any_of(MEMBER_WRITE))
        required_roles, owner_allowed = GROUP_ADMIN, True
    else:
        raise GraphError(400, "Request_UnsupportedQuery", "Unsupported directory operation")
    if delegated and required_roles and not roles & required_roles:
        owned = None
        if owner_allowed and target_id is not None:
            owned = await session.scalar(
                select(DirectoryRelation.object_id).where(
                    DirectoryRelation.tenant_id == context.tenant_id,
                    DirectoryRelation.group_id == target_id,
                    DirectoryRelation.object_id == context.subject_id,
                    DirectoryRelation.relation == "owner",
                )
            )
        if owned is None:
            deny()


def readable_fields(
    context: PrincipalContext, kind: str, object_id: UUID | None = None
) -> frozenset[str]:
    if kind == "user":
        if context.permissions & USER_READ:
            return USER_FIELDS | {"signInActivity"}
        if (
            context.token_kind == "delegated"
            and context.subject_id == object_id
            and context.permissions & {"User.Read", "User.ReadWrite"}
        ):
            return USER_FIELDS | {"signInActivity"}
        return USER_BASIC if "User.ReadBasic.All" in context.permissions else frozenset({"id"})
    if kind == "group":
        if context.permissions & GROUP_FULL:
            return GROUP_FIELDS
        return GROUP_BASIC if context.permissions & MEMBER_READ else frozenset({"id"})
    if kind == "servicePrincipal":
        if context.permissions & {
            "Application.Read.All",
            "Application.ReadWrite.All",
            "Directory.Read.All",
            "Directory.ReadWrite.All",
        }:
            return frozenset({"id", "displayName", "appId", "accountEnabled"})
        return frozenset({"id"})
    return frozenset({"id"})

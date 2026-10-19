"""Current ARM identities and inherited control-plane role permissions."""

from fnmatch import fnmatchcase
from uuid import UUID

from sqlalchemy import and_, func, or_, select

from mockazure.authorization.evaluator import AuthorizationDenied, require_current_identity
from mockazure.directory.models import DirectoryRelation, Group
from mockazure.endpoints import RESOURCES
from mockazure.identity.models import ServicePrincipal
from mockazure.providers.arm.contracts import builtin_roles
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.models import ArmResource, RoleAssignment, Subscription

ARM = next(r.audience for r in RESOURCES if r.service == "arm")


async def current_principal(session, actor, *, audience=ARM, delegated_scope="user_impersonation"):
    if actor.audience != audience or actor.token_kind not in {"application", "delegated"}:
        raise ArmError(403, "AuthorizationFailed", "ARM identity is required")
    if actor.token_kind == "delegated" and delegated_scope not in actor.permissions:
        raise ArmError(403, "AuthorizationFailed", "ARM delegated scope is required")
    try:
        await require_current_identity(actor, session)
    except AuthorizationDenied:
        raise ArmError(403, "AuthorizationFailed", "Current ARM identity is unavailable") from None
    if actor.token_kind == "delegated":
        return actor.subject_id
    return await session.scalar(
        select(ServicePrincipal.id).where(
            ServicePrincipal.tenant_id == actor.tenant_id,
            ServicePrincipal.client_id == actor.client_id,
        )
    )


def security_groups(tenant, principal_id):
    """A UNION recursive CTE deduplicates cycles and excludes deleted/nonsecurity hops."""
    active = (
        Group.tenant_id == tenant,
        Group.deleted_at.is_(None),
        func.coalesce(Group.profile["securityEnabled"].as_boolean(), True).is_(True),
    )
    edges = (DirectoryRelation.tenant_id == tenant, DirectoryRelation.relation == "member")
    base = (
        select(DirectoryRelation.group_id.label("id"))
        .join(Group, and_(Group.tenant_id == tenant, Group.id == DirectoryRelation.group_id))
        .where(*active, *edges, DirectoryRelation.object_id == principal_id)
    )
    tree = base.cte(recursive=True)
    tree = tree.union(
        select(DirectoryRelation.group_id)
        .join(tree, DirectoryRelation.object_id == tree.c.id)
        .join(Group, and_(Group.tenant_id == tenant, Group.id == DirectoryRelation.group_id))
        .where(*active, *edges)
    )
    return select(tree.c.id)


def permitted_roles(action, *, data=False):
    action = action.lower()
    return [
        role["id"]
        for role in builtin_roles().values()
        if any(
            fnmatchcase(action, pattern.lower())
            for pattern in role["dataActions" if data else "actions"]
        )
        and not any(
            fnmatchcase(action, pattern.lower())
            for pattern in role["notDataActions" if data else "notActions"]
        )
    ]


async def grant_query(
    session, actor, action, *, audience=ARM, data=False, delegated_scope="user_impersonation"
):
    principal_id = await current_principal(
        session, actor, audience=audience, delegated_scope=delegated_scope
    )
    direct = (
        RoleAssignment.client_id == actor.client_id
        if actor.token_kind == "application"
        else RoleAssignment.user_id == actor.subject_id
    )
    return select(RoleAssignment.id).where(
        RoleAssignment.tenant_id == actor.tenant_id,
        RoleAssignment.role_id.in_(permitted_roles(action, data=data)),
        or_(direct, RoleAssignment.group_id.in_(security_groups(actor.tenant_id, principal_id))),
    )


async def require_action(session, actor, scope, action):
    from mockazure.providers.arm.inventory import resolve_scope

    await resolve_scope(session, actor.tenant_id, scope)
    subscription = await session.get(Subscription, scope.subscription_id)
    if not action.lower().endswith("/read") and subscription.state != "Enabled":
        raise ArmError(403, "ReadOnlyDisabledSubscription", "Subscription does not permit writes")
    query = await grant_query(session, actor, action)
    allowed = await session.scalar(
        query.where(RoleAssignment.scope_key.in_((*scope.parent_keys, scope.key))).limit(1)
    )
    if allowed is None:
        raise ArmError(403, "AuthorizationFailed", "The current identity lacks scoped ARM access")


async def readable_scopes(session, actor, action):
    query = await grant_query(session, actor, action)
    return (
        query.where(
            RoleAssignment.subscription_id == ArmResource.subscription_id,
            or_(
                RoleAssignment.scope_key == ArmResource.scope_key,
                func.left(ArmResource.scope_key, func.length(RoleAssignment.scope_key) + 1)
                == RoleAssignment.scope_key + "/",
            ),
        )
        .correlate(ArmResource)
        .exists()
    )


async def resolve_assignee(session, tenant, principal_id: UUID, principal_type: str):
    from mockazure.identity.models import User

    if principal_type == "ServicePrincipal":
        row = await session.get(ServicePrincipal, principal_id)
        field, value = "client_id", row.client_id if row else None
    elif principal_type == "User":
        row = await session.get(User, principal_id)
        field, value = "user_id", principal_id
    elif principal_type == "Group":
        row = await session.get(Group, principal_id)
        field, value = "group_id", principal_id
    else:
        raise ArmError(400, "InvalidPrincipalType", "Unsupported ARM principal type")
    if row is None or row.tenant_id != tenant or row.deleted_at is not None:
        raise ArmError(404, "PrincipalNotFound", "Principal was not found in this tenant")
    if isinstance(row, Group) and row.profile.get("securityEnabled", True) is not True:
        raise ArmError(400, "InvalidPrincipalType", "A security group is required")
    return {field: value}

"""Canonical ARM assignment writes shared by local control and public RBAC."""

from sqlalchemy import select

from mockazure.providers.arm.authorization import resolve_assignee
from mockazure.providers.arm.contracts import builtin_roles
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import lock_subscription, resolve_scope
from mockazure.providers.arm.models import ArmResource, RoleAssignment
from mockazure.providers.arm.scopes import parse_scope


def assignment_view(row, principal_id, principal_type):
    return {
        "id": row.scope_key + "/providers/Microsoft.Authorization/roleAssignments/" + str(row.id),
        "name": str(row.id),
        "type": "Microsoft.Authorization/roleAssignments",
        "properties": {
            "scope": row.scope_key,
            "principalId": str(principal_id),
            "principalType": principal_type,
            "roleDefinitionId": f"/subscriptions/{row.subscription_id}/providers/"
            f"Microsoft.Authorization/roleDefinitions/{row.role_id}",
            **({"description": row.description} if row.description is not None else {}),
        },
    }


async def set_assignment(
    session,
    tenant,
    subscription_id,
    assignment_id,
    scope,
    role_id,
    principal_id,
    principal_type,
    description=None,
):
    await lock_subscription(session, tenant, subscription_id)
    if scope.subscription_id != subscription_id:
        raise ArmError(400, "InvalidScope", "Assignment scope is outside its subscription")
    owned = await resolve_scope(session, tenant, scope)
    if isinstance(owned, ArmResource):
        group = (
            await resolve_scope(session, tenant, parse_scope(owned.group_key))
            if owned.group_key
            else owned
        )
        if group.provisioning_state == "Deleting":
            raise ArmError(
                409, "ResourceGroupBeingDeleted", "Resource group deletion is in progress"
            )
    if role_id not in {r["id"] for r in builtin_roles().values()}:
        raise ArmError(400, "RoleDefinitionDoesNotExist", "Unsupported role definition")
    principal = await resolve_assignee(session, tenant, principal_id, principal_type)
    row = await session.get(RoleAssignment, assignment_id)
    key, value = next(iter(principal.items()))
    if row:
        if row.tenant_id != tenant or row.subscription_id != subscription_id:
            raise ArmError(404, "RoleAssignmentNotFound", "Role assignment was not found")
        if row.scope_key != scope.key or row.role_id != role_id or getattr(row, key) != value:
            raise ArmError(
                409, "RoleAssignmentUpdateNotPermitted", "Assignment identity is immutable"
            )
        row.description = description
    else:
        duplicate = await session.scalar(
            select(RoleAssignment.id).where(
                RoleAssignment.tenant_id == tenant,
                RoleAssignment.scope_key == scope.key,
                RoleAssignment.role_id == role_id,
                getattr(RoleAssignment, key) == value,
            )
        )
        if duplicate is not None:
            raise ArmError(409, "RoleAssignmentExists", "Equivalent role assignment already exists")
        row = RoleAssignment(
            id=assignment_id,
            tenant_id=tenant,
            subscription_id=subscription_id,
            scope_key=scope.key,
            inventory_key=scope.key if scope.group is not None else None,
            role_id=role_id,
            description=description,
            **principal,
        )
        session.add(row)
    await session.flush()
    return row

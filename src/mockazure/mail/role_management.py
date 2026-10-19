"""Exchange built-in role membership and assignments with live effective authority."""

from datetime import datetime
from uuid import uuid4, uuid5

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.admin.errors import AdminError
from mockazure.directory.models import DirectoryRelation, Group
from mockazure.identity.models import User
from mockazure.identity.types import PrincipalContext
from mockazure.mail.delegation import effective_principals, lock_delegation, resolve_trustee
from mockazure.mail.role_management_contracts import GROUP_ROLES, ROLE_COMMANDS
from mockazure.mail.role_management_models import ExchangeRoleAssignment, ExchangeRoleMember
from mockazure.mail.role_management_schemas import RoleAssignmentInput, RoleMemberInput, RoleState
from mockazure.mail.security_configuration import configuration_audit
from mockazure.providers.graph.errors import GraphError


def invalid(message):
    raise GraphError(400, "InvalidParameter", message)


def canonical_role(identity):
    match = next(
        (
            name
            for name in ROLE_COMMANDS
            if isinstance(identity, str) and name.casefold() == identity.casefold()
        ),
        None,
    )
    if match is None:
        invalid("Unsupported management role")
    return match


def group_id(tenant, name):
    return uuid5(tenant, "exchange:role-group:" + name)


def role_group(tenant, identity):
    match = next(
        (
            name
            for name in GROUP_ROLES
            if isinstance(identity, str)
            and identity.casefold() in {name.casefold(), str(group_id(tenant, name))}
        ),
        None,
    )
    if match is None:
        raise GraphError(404, "ManagementObjectNotFoundException", "Role group not found")
    return match


async def current_principal(session, tenant, identifier):
    user = await session.get(User, identifier)
    if user is not None and user.tenant_id == tenant and user.deleted_at is None:
        return "User", user.upn, user.display_name
    group = await session.get(Group, identifier)
    if (
        group is not None
        and group.tenant_id == tenant
        and group.deleted_at is None
        and group.profile.get("securityEnabled", True)
    ):
        return "SecurityGroup", group.display_name, group.display_name
    return None


async def principal(session, tenant, identity, expected=None):
    key, _ = await resolve_trustee(session, tenant, identity)
    value = await current_principal(session, tenant, key)
    if value is None or expected is not None and value[0] != expected:
        invalid("Current user or security group required")
    return key, value


async def assignments(session, tenant):
    return list(
        await session.scalars(
            select(ExchangeRoleAssignment)
            .where(ExchangeRoleAssignment.tenant_id == tenant)
            .order_by(ExchangeRoleAssignment.id)
        )
    )


async def live_members(session, tenant, group=None):
    query = select(ExchangeRoleMember).where(ExchangeRoleMember.tenant_id == tenant)
    if group is not None:
        query = query.where(ExchangeRoleMember.group_name == group)
    return [
        r
        for r in await session.scalars(
            query.order_by(ExchangeRoleMember.group_name, ExchangeRoleMember.principal_id)
        )
        if await current_principal(session, tenant, r.principal_id)
    ]


async def scope_contains(session, tenant, group_id, mailbox):
    group = await session.get(Group, group_id)
    if group is None or group.tenant_id != tenant or group.deleted_at is not None:
        return False
    return (
        await session.scalar(
            select(DirectoryRelation.object_id).where(
                DirectoryRelation.tenant_id == tenant,
                DirectoryRelation.group_id == group_id,
                DirectoryRelation.object_id == mailbox,
                DirectoryRelation.relation == "member",
            )
        )
        is not None
    )


async def permits_command(session, actor, command, mailbox=None, *, tenant_wide=False):
    # App data-access roles are deliberately separate from this administrator-role model.
    if actor.token_kind != "delegated" or actor.subject_id is None:
        return False
    principals = await effective_principals(session, actor.tenant_id, actor.subject_id)
    memberships = {
        r.group_name
        for r in await live_members(session, actor.tenant_id)
        if r.principal_id in principals
    }
    role_scopes = [(role, None) for group in memberships for role in GROUP_ROLES[group]]
    for assignment in await assignments(session, actor.tenant_id):
        if assignment.group_name in memberships or assignment.principal_id in principals:
            if assignment.principal_id is not None and not await current_principal(
                session, actor.tenant_id, assignment.principal_id
            ):
                continue
            role_scopes.append((assignment.role, assignment.recipient_group_id))
    for role, scope in role_scopes:
        if command not in ROLE_COMMANDS[role]:
            continue
        if scope is None:
            return True
        group = await session.get(Group, scope)
        if group is None or group.tenant_id != actor.tenant_id or group.deleted_at is not None:
            continue
        # RecipientGroupScope restricts writes; the role's implicit read scope remains organization.
        if command.startswith("Get-"):
            return True
        if tenant_wide:
            continue
        if mailbox is None:
            return True  # command advertisement; each invocation checks target
        if await scope_contains(session, actor.tenant_id, scope, mailbox):
            return True
    return False


async def assignment_view(session, tenant, row):
    who = await current_principal(session, tenant, row.principal_id) if row.principal_id else None
    if row.principal_id and who is None:
        return None
    return {
        "Identity": row.name,
        "Name": row.name,
        "Guid": str(row.id),
        "Role": row.role,
        "RoleAssigneeName": row.group_name or who[1],
        "RoleAssigneeType": "RoleGroup" if row.group_name else who[0],
        "RoleAssignee": str(group_id(tenant, row.group_name))
        if row.group_name
        else str(row.principal_id),
        "Enabled": True,
        "Delegating": False,
        "RecipientReadScope": "Organization",
        "RecipientWriteScope": "CustomRecipientScope" if row.recipient_group_id else "Organization",
        "RecipientGroupScope": str(row.recipient_group_id) if row.recipient_group_id else None,
    }


def default_assignments(tenant):
    return [
        {
            "Identity": role + "-" + group,
            "Name": role + "-" + group,
            "Guid": str(uuid5(tenant, "exchange:default-assignment:" + group + ":" + role)),
            "Role": role,
            "RoleAssigneeName": group,
            "RoleAssigneeType": "RoleGroup",
            "RoleAssignee": str(group_id(tenant, group)),
            "Enabled": True,
            "Delegating": False,
            "RecipientReadScope": "Organization",
            "RecipientWriteScope": "Organization",
            "RecipientGroupScope": None,
        }
        for group, roles in GROUP_ROLES.items()
        for role in roles
    ]


async def invoke_role_management(
    session: AsyncSession, actor: PrincipalContext, command: str, parameters: dict, now: datetime
) -> list[dict]:
    from mockazure.mail.authorization import require_exchange_command
    from mockazure.providers.exchange.commands import result_size

    await lock_delegation(session, actor.tenant_id)
    await require_exchange_command(session, actor, command, tenant_wide=True)
    tenant = actor.tenant_id
    if command == "Get-RoleGroup":
        groups = (
            [role_group(tenant, parameters["Identity"])]
            if "Identity" in parameters
            else list(GROUP_ROLES)
        )
        extra = await assignments(session, tenant)
        return [
            {
                "Identity": name,
                "Name": name,
                "Guid": str(group_id(tenant, name)),
                "Roles": sorted(
                    set(GROUP_ROLES[name]) | {a.role for a in extra if a.group_name == name}
                ),
                "Members": [str(r.principal_id) for r in await live_members(session, tenant, name)],
                "ManagedBy": [],
            }
            for name in groups[: result_size(parameters)]
        ]
    if command.endswith("RoleGroupMember"):
        if not parameters.get("Identity"):
            invalid("Identity is required")
        group = role_group(tenant, parameters["Identity"])
        if command == "Get-RoleGroupMember":
            result = []
            for row in (await live_members(session, tenant, group))[: result_size(parameters)]:
                _, identity, name = await current_principal(session, tenant, row.principal_id)
                result.append(
                    {
                        "Identity": identity,
                        "Name": name,
                        "Guid": str(row.principal_id),
                        "ExternalDirectoryObjectId": str(row.principal_id),
                    }
                )
            return result
        if not parameters.get("Member"):
            invalid("Member is required")
        key, _ = await principal(session, tenant, parameters["Member"])
        before = {
            "Members": [str(r.principal_id) for r in await live_members(session, tenant, group)]
        }
        row = await session.get(ExchangeRoleMember, (tenant, group, key))
        if command == "Add-RoleGroupMember":
            if row is not None:
                raise GraphError(
                    409, "MemberAlreadyExists", "Role group already contains this member"
                )
            session.add(ExchangeRoleMember(tenant_id=tenant, group_name=group, principal_id=key))
        else:
            if row is None:
                raise GraphError(404, "MemberNotFound", "Role group does not contain this member")
            await session.delete(row)
        await session.flush()
        after = {
            "Members": [str(r.principal_id) for r in await live_members(session, tenant, group)]
        }
        await configuration_audit(session, actor, command, group, before, after, now)
        return []
    rows = await assignments(session, tenant)
    if command == "Get-ManagementRoleAssignment":
        if "Identity" in parameters and {"Role", "RoleAssignee"} & parameters.keys():
            invalid("Identity and role-assignee parameter sets conflict")
        result = default_assignments(tenant)
        for row in rows:
            value = await assignment_view(session, tenant, row)
            if value:
                result.append(value)
        if "Identity" in parameters:
            key = parameters["Identity"].casefold()
            result = [r for r in result if key in {r["Name"].casefold(), r["Guid"]}]
            if not result:
                raise GraphError(
                    404, "ManagementObjectNotFoundException", "Role assignment not found"
                )
        if "Role" in parameters:
            role = canonical_role(parameters["Role"])
            result = [r for r in result if r["Role"] == role]
        if "RoleAssignee" in parameters:
            key = parameters["RoleAssignee"].casefold()
            result = [
                r for r in result if key in {r["RoleAssigneeName"].casefold(), r["RoleAssignee"]}
            ]
        if "RoleAssigneeType" in parameters:
            if parameters["RoleAssigneeType"] not in {"User", "SecurityGroup", "RoleGroup"}:
                invalid("Unsupported role assignee type")
            result = [r for r in result if r["RoleAssigneeType"] == parameters["RoleAssigneeType"]]
        for field in ("Enabled", "Delegating"):
            if field in parameters:
                result = [r for r in result if r[field] == parameters[field]]
        if "RecipientGroupScope" in parameters:
            key, _ = await principal(
                session, tenant, parameters["RecipientGroupScope"], "SecurityGroup"
            )
            result = [r for r in result if r["RecipientGroupScope"] == str(key)]
        return result
    if command == "New-ManagementRoleAssignment":
        role = canonical_role(parameters.get("Role"))
        if bool(parameters.get("User")) == bool(parameters.get("SecurityGroup")):
            invalid("Exactly one User or SecurityGroup assignee is required")
        if parameters.get("RecipientRelativeWriteScope", "Organization") != "Organization":
            invalid("Only Organization relative write scope is supported")
        if "RecipientGroupScope" in parameters and "RecipientRelativeWriteScope" in parameters:
            invalid("Scope parameters conflict")
        group = None
        key = None
        if "User" in parameters:
            key, who = await principal(session, tenant, parameters["User"], "User")
        else:
            try:
                group = role_group(tenant, parameters["SecurityGroup"])
            except GraphError:
                key, who = await principal(
                    session, tenant, parameters["SecurityGroup"], "SecurityGroup"
                )
        scope = None
        if "RecipientGroupScope" in parameters:
            scope, _ = await principal(
                session, tenant, parameters["RecipientGroupScope"], "SecurityGroup"
            )
        name = parameters.get("Name", role + "-" + (group or str(key)))
        try:
            value = RoleAssignmentInput(
                id=uuid4(),
                name=name,
                role=role,
                principal_id=key,
                group_name=group,
                recipient_group_id=scope,
            )
        except ValidationError:
            invalid("Invalid role assignment")
        if any(r.name.casefold() == name.casefold() for r in rows) or any(
            r["Name"].casefold() == name.casefold() for r in default_assignments(tenant)
        ):
            raise GraphError(409, "ObjectAlreadyExists", "Role assignment name exists")
        row = ExchangeRoleAssignment(
            tenant_id=tenant, name_key=name.casefold(), **value.model_dump()
        )
        session.add(row)
        await session.flush()
        result = await assignment_view(session, tenant, row)
        await configuration_audit(session, actor, command, name, {}, result, now)
        return [result]
    if command == "Remove-ManagementRoleAssignment":
        identity = parameters.get("Identity")
        if not identity:
            invalid("Identity is required")
        matched = [r for r in rows if identity.casefold() in {r.name.casefold(), str(r.id)}]
        if not matched:
            if any(
                identity.casefold() in {r["Name"].casefold(), r["Guid"]}
                for r in default_assignments(tenant)
            ):
                invalid("Built-in baseline assignments are immutable in this profile")
            raise GraphError(404, "ManagementObjectNotFoundException", "Role assignment not found")
        if len(matched) != 1:
            invalid("Ambiguous role assignment")
        row = matched[0]
        before = await assignment_view(session, tenant, row)
        await session.delete(row)
        await configuration_audit(
            session, actor, command, row.name, before or {"Name": row.name}, {}, now
        )
        return []
    invalid("Unsupported role command")


async def export_roles(session, tenant):
    await lock_delegation(session, tenant)
    members = [
        RoleMemberInput.model_validate(r, from_attributes=True).model_dump(mode="json")
        for r in await live_members(session, tenant)
    ]
    exported = []
    for row in await assignments(session, tenant):
        if row.principal_id and not await current_principal(session, tenant, row.principal_id):
            continue
        if row.recipient_group_id:
            group = await session.get(Group, row.recipient_group_id)
            if group is None or group.deleted_at is not None:
                continue
        exported.append(
            RoleAssignmentInput.model_validate(row, from_attributes=True).model_dump(mode="json")
        )
    return {"members": members, "assignments": exported}


async def replace_roles(session, tenant, value: RoleState):
    await lock_delegation(session, tenant)
    for member in value.members:
        if not await current_principal(session, tenant, member.principal_id):
            raise AdminError(422, "InvalidRolePrincipal", "Current tenant principal required")
    defaults = default_assignments(tenant)
    for assignment in value.assignments:
        if assignment.principal_id and not await current_principal(
            session, tenant, assignment.principal_id
        ):
            raise AdminError(422, "InvalidRolePrincipal", "Current tenant principal required")
        if any(
            assignment.name.casefold() == r["Name"].casefold() or str(assignment.id) == r["Guid"]
            for r in defaults
        ):
            raise AdminError(422, "ReservedAssignment", "Built-in baseline assignment is immutable")
        if assignment.recipient_group_id:
            group = await current_principal(session, tenant, assignment.recipient_group_id)
            if group is None or group[0] != "SecurityGroup":
                raise AdminError(422, "InvalidRoleScope", "Current security group required")
    await session.execute(delete(ExchangeRoleMember).where(ExchangeRoleMember.tenant_id == tenant))
    await session.execute(
        delete(ExchangeRoleAssignment).where(ExchangeRoleAssignment.tenant_id == tenant)
    )
    for member in value.members:
        session.add(ExchangeRoleMember(tenant_id=tenant, **member.model_dump()))
    for assignment in value.assignments:
        session.add(
            ExchangeRoleAssignment(
                tenant_id=tenant, name_key=assignment.name.casefold(), **assignment.model_dump()
            )
        )
    await session.flush()
    return await export_roles(session, tenant)

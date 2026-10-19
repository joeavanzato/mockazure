"""Current operational delegation, mutation and send authority."""

from uuid import UUID

from sqlalchemy import delete, func, or_, select

from mockazure.authorization.evaluator import AuthorizationDenied, OperationRequirement, authorize
from mockazure.db.models import Tenant
from mockazure.directory.models import DirectoryRelation, Group
from mockazure.identity.models import User
from mockazure.identity.types import PrincipalContext
from mockazure.mail.authorization import GRAPH, require_mailbox_access
from mockazure.mail.delegation_models import MailboxDelegation
from mockazure.mail.facts import record_mail_fact
from mockazure.mail.models import MailSettings
from mockazure.mail.repository import resolve_mailbox
from mockazure.providers.graph.errors import GraphError


async def lock_delegation(session, tenant):
    await session.get(Tenant, tenant, with_for_update=True)


async def resolve_trustee(session, tenant, identity):
    if not isinstance(identity, str) or not identity or len(identity) > 320 or "\0" in identity:
        raise GraphError(400, "InvalidTrustee", "Invalid delegation principal")
    try:
        key = UUID(identity)
    except ValueError:
        key = None
    matches = [
        func.lower(User.upn) == identity.casefold(),
        func.lower(User.display_name) == identity.casefold(),
    ]
    if key:
        matches.append(User.id == key)
    users = list(
        await session.scalars(
            select(User)
            .where(User.tenant_id == tenant, User.deleted_at.is_(None), or_(*matches))
            .limit(2)
        )
    )
    groups = [
        func.lower(Group.display_name) == identity.casefold(),
        func.lower(Group.mail_nickname) == identity.casefold(),
        func.lower(Group.profile["mail"].astext) == identity.casefold(),
    ]
    if key:
        groups.append(Group.id == key)
    group_rows = list(
        await session.scalars(
            select(Group)
            .where(
                Group.tenant_id == tenant,
                Group.deleted_at.is_(None),
                func.coalesce(Group.profile["securityEnabled"].as_boolean(), True).is_(True),
                or_(*groups),
            )
            .limit(2)
        )
    )
    rows = [(v.id, v.upn) for v in users] + [
        (v.id, v.profile.get("mail") or v.display_name) for v in group_rows
    ]
    # Resolve mailbox aliases even when a directory name matched. A display name
    # must not shadow a different principal's SMTP identity.
    try:
        box = await resolve_mailbox(session, tenant, identity)
    except GraphError as exc:
        if exc.status != 404:
            raise GraphError(400, "AmbiguousTrustee", "Delegation principal is ambiguous") from None
    else:
        user = await session.get(User, box.user_id)
        rows.append((user.id, user.upn))
    candidates = dict(rows)
    if not candidates:
        raise GraphError(404, "TrusteeNotFound", "Current delegation principal was not found")
    if len(candidates) != 1:
        raise GraphError(400, "AmbiguousTrustee", "Delegation principal is ambiguous")
    return next(iter(candidates.items()))


async def effective_principals(session, tenant, user_id):
    # UNION (not UNION ALL) also terminates cyclic group fixtures.
    edge = (
        select(DirectoryRelation.group_id.label("id"))
        .join(
            Group,
            (Group.tenant_id == DirectoryRelation.tenant_id)
            & (Group.id == DirectoryRelation.group_id),
        )
        .where(
            DirectoryRelation.tenant_id == tenant,
            DirectoryRelation.relation == "member",
            Group.deleted_at.is_(None),
            func.coalesce(Group.profile["securityEnabled"].as_boolean(), True).is_(True),
        )
    )
    parents = edge.where(DirectoryRelation.object_id == user_id).cte(recursive=True)
    parents = parents.union(edge.join(parents, DirectoryRelation.object_id == parents.c.id))
    return {user_id, *await session.scalars(select(parents.c.id))}


async def has_delegation(session, tenant, mailbox, principal, permission):
    principals = await effective_principals(session, tenant, principal)
    rows = list(
        await session.scalars(
            select(MailboxDelegation).where(
                MailboxDelegation.tenant_id == tenant,
                MailboxDelegation.mailbox_id == mailbox,
                MailboxDelegation.principal_id.in_(principals),
                MailboxDelegation.permission == permission,
            )
        )
    )
    # Children-only ACEs don't grant access to this mailbox object. Deny wins.
    rows = [v for v in rows if v.inheritance in {"None", "All", "SelfAndChildren"}]
    return bool(rows) and not any(v.deny for v in rows)


async def require_send_identity(
    session, actor: PrincipalContext, mailbox_id: UUID, represented_id: UUID, mode: str
) -> None:
    if mode not in {"sendAs", "sendOnBehalf"} or actor.audience != GRAPH:
        raise AuthorizationDenied("Unsupported sending mode or audience")
    delegated = (
        {"Mail.Send", "Mail.Send.Shared"}
        if represented_id == actor.subject_id
        else {"Mail.Send.Shared"}
    )
    await authorize(
        actor,
        OperationRequirement("graph", frozenset({"Mail.Send"}), frozenset(delegated)),
        session,
    )
    settings = await session.get(MailSettings, actor.tenant_id)
    if not settings or not settings.exchange_enabled:
        raise AuthorizationDenied("Exchange entitlement is required")
    try:
        await resolve_mailbox(session, actor.tenant_id, str(mailbox_id))
        await resolve_mailbox(session, actor.tenant_id, str(represented_id))
    except GraphError:
        raise AuthorizationDenied("Current sending mailbox is required") from None
    if actor.token_kind == "application":
        if mode != "sendAs" or mailbox_id != represented_id:
            raise AuthorizationDenied("Application sending requires the represented mailbox")
        return
    if mailbox_id != actor.subject_id and not await has_delegation(
        session, actor.tenant_id, mailbox_id, actor.subject_id, "FullAccess"
    ):
        raise AuthorizationDenied("FullAccess is required for the sending mailbox")
    if represented_id == actor.subject_id:
        return
    permission = {"sendAs": "SendAs", "sendOnBehalf": "SendOnBehalf"}[mode]
    if not await has_delegation(
        session, actor.tenant_id, represented_id, actor.subject_id, permission
    ):
        raise AuthorizationDenied("Current send delegation is required")


async def delegation_rows(session, box, recipient=False):
    rows = await session.scalars(
        select(MailboxDelegation)
        .where(
            MailboxDelegation.tenant_id == box.tenant_id,
            MailboxDelegation.mailbox_id == box.user_id,
            MailboxDelegation.permission == ("SendAs" if recipient else "FullAccess"),
        )
        .order_by(
            MailboxDelegation.principal_id, MailboxDelegation.deny, MailboxDelegation.inheritance
        )
    )
    result = []
    for row in rows:
        try:
            _, name = await resolve_trustee(session, box.tenant_id, str(row.principal_id))
        except GraphError:
            # Orphaned/deleted principals cannot authorize but retain observable ACE evidence.
            name = str(row.principal_id)
        value = {
            "Identity": box.primary_smtp,
            "Trustee" if recipient else "User": name,
            "AccessRights": [row.permission],
            "IsInherited": False,
            "InheritanceType": row.inheritance,
        }
        if recipient:
            value["AccessControlType"] = "Allow"
        else:
            value["Deny"] = row.deny
        result.append(value)
    return result


async def mutate_permission(session, actor, name, values, now):
    recipient = "Recipient" in name
    field, permission = ("Trustee", "SendAs") if recipient else ("User", "FullAccess")
    if (
        not values.get("Identity")
        or not values.get(field)
        or values.get("AccessRights") != [permission]
    ):
        raise GraphError(
            400, "InvalidParameter", f"Identity, {field} and {permission} are required"
        )
    inheritance = values.get("InheritanceType", "None" if recipient else "All")
    if inheritance not in {"None", "All", "Children", "Descendents", "SelfAndChildren"}:
        raise GraphError(400, "InvalidParameter", "Unsupported inheritance type")
    await lock_delegation(session, actor.tenant_id)
    box = await resolve_mailbox(session, actor.tenant_id, values["Identity"])
    await session.refresh(box, with_for_update=True)
    await require_mailbox_access(session, actor, box, name)
    principal, principal_name = await resolve_trustee(session, actor.tenant_id, values[field])
    key = (
        actor.tenant_id,
        box.user_id,
        principal,
        permission,
        values.get("Deny", False),
        inheritance,
    )
    row = await session.get(MailboxDelegation, key)
    before = {"Permissions": await delegation_rows(session, box, recipient)}
    if not recipient:
        before["AutoMapping"] = row.auto_mapping if row else None
    if name.startswith("Add-"):
        if row is None:
            row = MailboxDelegation(
                tenant_id=key[0],
                mailbox_id=key[1],
                principal_id=key[2],
                permission=permission,
                deny=key[4],
                inheritance=inheritance,
            )
            session.add(row)
        row.auto_mapping = False if recipient else values.get("AutoMapping", True)
    elif row is not None:
        await session.delete(row)
    await session.flush()
    after = {"Permissions": await delegation_rows(session, box, recipient)}
    if not recipient:
        after["AutoMapping"] = row.auto_mapping if name.startswith("Add-") else None
    user = await session.get(User, box.user_id)
    await record_mail_fact(
        session,
        actor,
        name,
        {"id": str(box.user_id), "upn": user.upn, "primarySmtpAddress": box.primary_smtp},
        before,
        after,
        now,
    )
    return (
        [
            v
            for v in after["Permissions"]
            if v[field] == principal_name
            and v["InheritanceType"] == inheritance
            and v.get("Deny", False) == values.get("Deny", False)
        ]
        if name.startswith("Add-")
        else []
    )


async def behalf_ids(session, box):
    return sorted(
        str(v)
        for v in await session.scalars(
            select(MailboxDelegation.principal_id).where(
                MailboxDelegation.tenant_id == box.tenant_id,
                MailboxDelegation.mailbox_id == box.user_id,
                MailboxDelegation.permission == "SendOnBehalf",
            )
        )
    )


async def replace_behalf(session, box, value):
    async def ids(values):
        values = [values] if isinstance(values, str) else values
        if not isinstance(values, list) or len(values) > 1000:
            raise GraphError(400, "InvalidParameter", "Expected at most 1000 delegate identities")
        return {str((await resolve_trustee(session, box.tenant_id, v))[0]) for v in values}

    if isinstance(value, dict):
        canonical = {k.casefold(): v for k, v in value.items()}
        if not canonical or canonical.keys() - {"add", "remove"} or len(canonical) != len(value):
            raise GraphError(
                400, "InvalidParameter", "Expected Add and/or Remove delegate identities"
            )
        add, remove = await ids(canonical.get("add", [])), await ids(canonical.get("remove", []))
        if add & remove:
            raise GraphError(400, "InvalidParameter", "Cannot add and remove the same delegate")
        target = (set(await behalf_ids(session, box)) - remove) | add
    else:
        target = await ids([] if value is None else value)
    if len(target) > 1000:
        raise GraphError(400, "InvalidParameter", "Too many delegates")
    await session.execute(
        delete(MailboxDelegation).where(
            MailboxDelegation.tenant_id == box.tenant_id,
            MailboxDelegation.mailbox_id == box.user_id,
            MailboxDelegation.permission == "SendOnBehalf",
        )
    )
    for principal in target:
        session.add(
            MailboxDelegation(
                tenant_id=box.tenant_id,
                mailbox_id=box.user_id,
                principal_id=UUID(principal),
                permission="SendOnBehalf",
                deny=False,
                inheritance="None",
                auto_mapping=False,
            )
        )
    await session.flush()

"""Operation-specific v1.0 write authority; imported roster rows confer no authority."""

from sqlalchemy import select

from mockazure.authorization.evaluator import AuthorizationDenied, require_current_identity
from mockazure.providers.graph.errors import GraphError
from mockazure.workloads.models import ConversationAccess as A
from mockazure.workloads.team_access import member_exists


def denied(message="The operation requires current Graph permission and conversation authority"):
    raise GraphError(403, "Forbidden", message)


async def current_identity(session, actor):
    try:
        await require_current_identity(actor, session)
    except AuthorizationDenied:
        denied("An active local application and user are required")


async def authority(session, actor, row, permission="member", user=None):
    if permission == "member":
        direct = await session.scalar(
            select(member_exists(actor, row.kind, row.payload["id"], row.scope, user=user))
        )
        if direct:
            return True
        if row.kind == "channel" and row.payload.get("membershipType", "standard") == "standard":
            return bool(
                await session.scalar(
                    select(member_exists(actor, "team", row.scope, row.scope, user=user))
                )
            )
        return False
    return bool(
        await session.scalar(
            select(A.id)
            .where(
                A.tenant_id == actor.tenant_id,
                A.scope == row.scope,
                A.resource_kind == row.kind,
                A.resource_id == row.payload["id"],
                A.principal_kind == "user",
                A.principal_id == (user or actor.subject_id),
                A.permission == permission,
            )
            .limit(1)
        )
    )


async def require_write(session, actor, operation, row=None):
    await current_identity(session, actor)
    delegated = actor.token_kind == "delegated"
    category, action = operation.split(".")[1], operation.rsplit(".", 1)[-1]
    if operation == "teams.chat.create":
        allowed = {"Chat.Create", "Chat.ReadWrite"} if delegated else {"Chat.Create"}
        if not actor.permissions & allowed:
            denied()
        return
    if ".message." in operation:
        if not delegated:
            denied("Ordinary message writes require delegated authority; migration is unsupported")
        category = "channel" if category in {"channel", "reply"} else "chat"
        if action == "send":
            allowed = (
                {"ChannelMessage.Send", "Group.ReadWrite.All"}
                if category == "channel"
                else {"ChatMessage.Send", "Chat.ReadWrite", "Group.ReadWrite.All"}
            )
        elif action == "update":
            allowed = (
                {"ChannelMessage.ReadWrite", "Group.ReadWrite.All"}
                if category == "channel"
                else {"Chat.ReadWrite"}
            )
        else:
            allowed = {"ChannelMessage.ReadWrite"} if category == "channel" else {"Chat.ReadWrite"}
        if not actor.permissions & allowed or not await authority(session, actor, row):
            denied()
        return
    rsc = None
    if category == "team":
        allowed = {"TeamMember.ReadWrite.All", "TeamMember.ReadWriteNonOwnerRole.All"}
    elif category == "channel":
        allowed = {"ChannelMember.ReadWrite.All"}
        if action == "update":
            allowed |= {"Directory.ReadWrite.All", "Group.ReadWrite.All"}
        rsc = "ChannelMember.ReadWrite.Group"
    elif category == "chat":
        allowed = (
            {"ChatMember.ReadWrite"} | ({"Chat.ReadWrite"} if action == "add" else set())
            if delegated
            else {"ChatMember.ReadWrite.All"}
            | ({"Chat.ReadWrite.All"} if action == "add" else set())
        )
        rsc = "Chat.Manage.Chat"
    else:
        denied()
    permitted = bool(actor.permissions & allowed)
    if not delegated and rsc and not permitted:
        permitted = bool(
            await session.scalar(
                select(A.id)
                .where(
                    A.tenant_id == actor.tenant_id,
                    A.scope == row.scope,
                    A.resource_kind == ("chat" if category == "chat" else "team"),
                    A.resource_id == row.scope,
                    A.principal_kind == "application",
                    A.principal_id == actor.client_id,
                    A.permission == rsc,
                )
                .limit(1)
            )
        )
    if not permitted or (delegated and not await authority(session, actor, row)):
        denied()


async def require_owner_change(session, actor, row, roles, old_roles=(), *, self_delete=False):
    if row.kind == "team" and "owner" in set(roles) | set(old_roles):
        if "TeamMember.ReadWrite.All" not in actor.permissions:
            denied("Changing an owner requires TeamMember.ReadWrite.All")
    if actor.token_kind == "delegated" and not self_delete:
        if not await authority(session, actor, row, "owner"):
            denied("This bounded member-management workflow requires an authoritative owner")

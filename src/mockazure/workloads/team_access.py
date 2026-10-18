"""Operation-specific OAuth permissions and separately provisioned Teams consent."""

from sqlalchemy import and_, exists, select

from mockazure.admin.errors import AdminError
from mockazure.authorization.evaluator import AuthorizationDenied, require_current_identity
from mockazure.identity.models import ServicePrincipal, User
from mockazure.providers.graph.errors import GraphError
from mockazure.workloads.models import ConversationAccess as A
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.repository import current_record, scope_row

RSC_PERMISSIONS = frozenset(
    {
        "TeamSettings.Read.Group",
        "TeamSettings.ReadWrite.Group",
        "TeamMember.Read.Group",
        "ChannelSettings.Read.Group",
        "ChannelSettings.ReadWrite.Group",
        "ChannelMember.Read.Group",
        "ChannelMessage.Read.Group",
        "File.Read.Group",
        "ChatSettings.Read.Chat",
        "ChatSettings.ReadWrite.Chat",
        "ChatMember.Read.Chat",
        "ChatMessage.Read.Chat",
        "ChannelMember.ReadWrite.Group",
        "Chat.Manage.Chat",
    }
)


def permissions(text):
    return frozenset(text.split())


LEGACY = "Directory.Read.All Directory.ReadWrite.All Group.Read.All Group.ReadWrite.All"
GROUP = "Group.Read.All Group.ReadWrite.All"
# Each row: delegated, tenant-wide application, resource-specific application.
OPERATIONS = {
    "team": (
        "Team.ReadBasic.All TeamSettings.Read.All TeamSettings.ReadWrite.All " + LEGACY,
        "Team.ReadBasic.All TeamSettings.Read.All TeamSettings.ReadWrite.All " + LEGACY,
        "TeamSettings.Read.Group TeamSettings.ReadWrite.Group",
    ),
    "joinedTeams": (
        "Team.ReadBasic.All TeamSettings.Read.All TeamSettings.ReadWrite.All "
        "User.Read.All User.ReadWrite.All Directory.Read.All Directory.ReadWrite.All",
        "Team.ReadBasic.All TeamSettings.Read.All TeamSettings.ReadWrite.All",
        "",
    ),
    "channel": (
        "Channel.ReadBasic.All ChannelSettings.Read.All ChannelSettings.ReadWrite.All " + LEGACY,
        "Channel.ReadBasic.All ChannelSettings.Read.All ChannelSettings.ReadWrite.All " + LEGACY,
        "ChannelSettings.Read.Group ChannelSettings.ReadWrite.Group",
    ),
    "teamMembers": (
        "TeamMember.Read.All TeamMember.ReadWrite.All",
        "TeamMember.Read.All TeamMember.ReadWrite.All",
        "TeamMember.Read.Group",
    ),
    "channelMembers": (
        "ChannelMember.Read.All ChannelMember.ReadWrite.All Group.Read.All",
        "ChannelMember.Read.All ChannelMember.ReadWrite.All",
        "ChannelMember.Read.Group ChannelMember.ReadWrite.Group",
    ),
    "channelMessages": (
        "ChannelMessage.Read.All " + GROUP,
        "ChannelMessage.Read.All " + GROUP,
        "ChannelMessage.Read.Group",
    ),
    "channelHosted": (
        "ChannelMessage.Read.All",
        "ChannelMessage.Read.All",
        "ChannelMessage.Read.Group",
    ),
    "chat": (
        "Chat.ReadBasic Chat.Read Chat.ReadWrite",
        "Chat.ReadBasic.All Chat.Read.All Chat.ReadWrite.All",
        "ChatSettings.Read.Chat ChatSettings.ReadWrite.Chat Chat.Manage.Chat",
    ),
    "chats": (
        "Chat.ReadBasic Chat.Read Chat.ReadWrite",
        "Chat.ReadBasic.All Chat.Read.All Chat.ReadWrite.All",
        "",
    ),
    "chatMembers": (
        "Chat.ReadBasic Chat.Read Chat.ReadWrite ChatMember.Read ChatMember.ReadWrite",
        "ChatMember.Read.All ChatMember.ReadWrite.All Chat.ReadBasic.All "
        "Chat.Read.All Chat.ReadWrite.All",
        "ChatMember.Read.Chat Chat.Manage.Chat",
    ),
    "chatMessages": (
        "Chat.Read Chat.ReadWrite",
        "Chat.Read.All Chat.ReadWrite.All",
        "ChatMessage.Read.Chat",
    ),
    "chatMessage": ("Chat.Read Chat.ReadWrite", "Chat.Read.All Chat.ReadWrite.All", ""),
    "chatHosted": ("Chat.Read Chat.ReadWrite", "Chat.Read.All Chat.ReadWrite.All", ""),
    "filesFolder": (
        "Files.Read Files.Read.All Files.ReadWrite Files.ReadWrite.All "
        "Sites.Read.All Sites.ReadWrite.All " + GROUP,
        "Files.Read.All Files.ReadWrite.All Sites.Read.All Sites.ReadWrite.All " + GROUP,
        "File.Read.Group",
    ),
}
OPERATIONS = {
    name: tuple(permissions(value) for value in values) for name, values in OPERATIONS.items()
}


def member_exists(actor, kind, identifier, scope, user=None):
    return exists(
        select(1).where(
            A.tenant_id == actor.tenant_id,
            A.scope == scope,
            A.resource_kind == kind,
            A.resource_id == identifier,
            A.principal_kind == "user",
            A.principal_id == (user or actor.subject_id),
            A.permission == "member",
        )
    )


def visibility(actor, row=V):
    if actor.token_kind == "application":
        return True
    return member_exists(actor, row.kind, row.payload["id"].as_string(), row.scope) | and_(
        row.kind == "channel",
        row.payload["membershipType"].as_string() == "standard",
        member_exists(actor, "team", row.scope, row.scope),
    )


async def check_operation(session, actor, operation, scope=None):
    try:
        await require_current_identity(actor, session)
    except AuthorizationDenied:
        raise GraphError(
            403, "Forbidden", "Current application or user identity is required"
        ) from None
    delegated, application, rsc = OPERATIONS[operation]
    if actor.permissions & (delegated if actor.token_kind == "delegated" else application):
        return
    if actor.token_kind == "application" and scope is not None and rsc:
        allowed = await session.scalar(
            select(A.id)
            .where(
                A.tenant_id == actor.tenant_id,
                A.scope == scope,
                A.principal_kind == "application",
                A.principal_id == actor.client_id,
                A.permission.in_(rsc),
            )
            .limit(1)
        )
        if allowed:
            return
    raise GraphError(
        403, "Forbidden", "The operation requires Graph permission or resource-specific consent"
    )


async def authorize_row(session, actor, row, operation):
    await scope_row(session, actor.tenant_id, "teams", row.scope, lock="read")
    await check_operation(session, actor, operation, row.scope)
    if actor.token_kind == "delegated" and not await session.scalar(
        select(visibility(actor)).where(
            V.tenant_id == actor.tenant_id,
            V.workload == "teams",
            V.scope == row.scope,
            V.kind == row.kind,
            V.id == row.id,
            V.valid_to.is_(None),
            V.deleted.is_(False),
        )
    ):
        raise GraphError(403, "Forbidden", "Explicit local conversation membership is required")


async def authorize_conversation(session, context, conversation_kind, conversation_id, operation):
    row = await current_record(
        session, context.tenant_id, "teams", conversation_kind, conversation_id
    )
    if row is None:
        raise GraphError(404, "NotFound", "Conversation was not found")
    await authorize_row(session, context, row, operation)


async def set_access(session, tenant, data):
    state = await scope_row(session, tenant, "teams", data.scope, lock=True)
    row = await session.scalar(
        select(V).where(
            V.tenant_id == tenant,
            V.workload == "teams",
            V.scope == data.scope,
            V.kind == data.resource_kind,
            V.payload["id"].as_string() == data.resource_id,
            V.valid_to.is_(None),
            V.deleted.is_(False),
        )
    )
    if state is None or row is None:
        raise AdminError(404, "ConversationNotFound", "Conversation was not found")
    if data.principal_kind == "user":
        valid = data.permission == "member"
        model, identity = User, User.id
    else:
        valid = data.permission in RSC_PERMISSIONS and (
            (data.resource_kind == "team" and data.permission.endswith(".Group"))
            or (data.resource_kind == "chat" and data.permission.endswith(".Chat"))
        )
        model, identity = ServicePrincipal, ServicePrincipal.client_id
    if not valid:
        raise AdminError(
            422, "InvalidConsent", "Permission does not apply to this resource or principal"
        )
    if not await session.scalar(
        select(model.id).where(
            model.tenant_id == tenant,
            identity == data.principal_id,
            model.enabled.is_(True),
            model.deleted_at.is_(None),
        )
    ):
        raise AdminError(422, "InvalidPrincipal", "Active local principal is required")
    values = data.model_dump()
    record = await session.scalar(
        select(A).where(
            A.tenant_id == tenant, *[getattr(A, key) == value for key, value in values.items()]
        )
    )
    if record is None:
        record = A(tenant_id=tenant, **values)
        session.add(record)
        state.grant_revision += 1
    await session.flush()
    return record

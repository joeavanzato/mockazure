"""Native Teams member/message operations over revisioned local conversation state."""

import copy
from uuid import uuid4

from sqlalchemy import func, select

from mockazure.evidence.repository import lock_state
from mockazure.providers.graph.errors import GraphError
from mockazure.workloads.facts import record_permission_fact
from mockazure.workloads.models import ConversationAccess as A
from mockazure.workloads.models import WorkloadRelation as E
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.repository import scope_row
from mockazure.workloads.team_mutation_contracts import (
    bad,
    fields,
    local_user,
    member_input,
    message_input,
    roles_input,
)
from mockazure.workloads.team_mutation_store import (
    add_edge,
    child_at,
    record_at,
    remove_member,
    set_membership,
    write_record,
)
from mockazure.workloads.team_write_access import (
    authority,
    denied,
    require_owner_change,
    require_write,
)


async def conversation_for(session, actor, category, ids):
    scope = ids["chat"] if category == "chat" else ids["team"]
    state = await scope_row(session, actor.tenant_id, "teams", scope, lock=True)
    if state is None:
        raise GraphError(404, "NotFound", "Conversation was not found")
    root = await record_at(
        session, actor.tenant_id, scope, "chat" if category == "chat" else "team", scope
    )
    conversation = root
    if category in {"channel", "reply"}:
        conversation = await child_at(session, actor.tenant_id, root, "channels", ids["channel"])
    return state, conversation


async def member_for_user(session, tenant, conversation, user):
    return await session.scalar(
        select(V)
        .join(
            E,
            (
                (E.tenant_id == V.tenant_id)
                & (E.workload == V.workload)
                & (E.scope == V.scope)
                & (E.target_kind == V.kind)
                & (E.target_id == V.id)
            ),
        )
        .where(
            V.tenant_id == tenant,
            V.workload == "teams",
            V.scope == conversation.scope,
            V.kind == "conversationMember",
            V.payload["userId"].as_string() == str(user),
            V.valid_to.is_(None),
            V.deleted.is_(False),
            E.source_kind == conversation.kind,
            E.source_id == conversation.id,
            E.relation == "members",
            E.valid_to.is_(None),
        )
    )


async def add_member(session, actor, state, conversation, user, roles):
    old = await member_for_user(session, actor.tenant_id, conversation, user.id)
    if old is not None and await authority(session, actor, conversation, user=user.id):
        raise GraphError(409, "Conflict", "User is already an authoritative member")
    identifier = old.payload["id"] if old else str(uuid4())
    payload = {
        "@odata.type": "#microsoft.graph.aadUserConversationMember",
        "id": identifier,
        "roles": roles,
        "userId": str(user.id),
        "displayName": user.display_name,
        "email": user.upn,
        "tenantId": str(actor.tenant_id),
    }
    row = await write_record(session, state, "conversationMember", payload, previous=old)
    if old is None:
        add_edge(session, state, conversation, "members", row)
    await set_membership(session, state, conversation, user.id, roles)
    return payload


async def create_chat(session, actor, payload, now):
    await require_write(session, actor, "teams.chat.create")
    fields(payload, {"chatType", "topic", "members"}, {"chatType", "members"})
    kind, members = payload["chatType"], payload["members"]
    if (
        kind not in {"oneOnOne", "group"}
        or not isinstance(members, list)
        or not 2 <= len(members) <= 250
    ):
        bad("Use a group or oneOnOne chat with 2 to 250 local members")
    if kind == "oneOnOne" and (len(members) != 2 or "topic" in payload):
        bad("A oneOnOne chat requires exactly two members and no topic")
    if "topic" in payload and (
        not isinstance(payload["topic"], str) or len(payload["topic"]) > 256
    ):
        bad("Chat topic exceeds the local 256-character bound")
    roster = [await member_input(session, actor.tenant_id, member, "chat") for member in members]
    ids = {user.id for user, _ in roster}
    if len(ids) != len(roster):
        bad("Duplicate chat member")
    if actor.token_kind == "delegated" and actor.subject_id not in ids:
        bad("The creating user must be included in the chat")
    if kind == "oneOnOne":
        candidates = (
            select(A.scope)
            .where(
                A.tenant_id == actor.tenant_id,
                A.resource_kind == "chat",
                A.permission == "member",
                A.principal_kind == "user",
                A.principal_id.in_(ids),
            )
            .group_by(A.scope)
            .having(func.count(func.distinct(A.principal_id)) == 2)
        )
        old = await session.scalar(
            select(V)
            .where(
                V.tenant_id == actor.tenant_id,
                V.workload == "teams",
                V.kind == "chat",
                V.scope.in_(candidates),
                V.payload["chatType"].as_string() == "oneOnOne",
                V.valid_to.is_(None),
                V.deleted.is_(False),
            )
            .limit(1)
        )
        if old:
            return copy.deepcopy(old.payload)
    identifier = "19:" + str(uuid4()) + "@thread.v2"
    state = await scope_row(session, actor.tenant_id, "teams", identifier, create=True, lock=True)
    state.revision += 1
    value = {
        "id": identifier,
        "chatType": kind,
        "topic": payload.get("topic"),
        "createdDateTime": now.isoformat(),
        "lastUpdatedDateTime": now.isoformat(),
    }
    chat = await write_record(session, state, "chat", value)
    for user, roles in roster:
        await add_member(session, actor, state, chat, user, roles)
    await record_permission_fact(
        session,
        actor,
        "teams.chat.create",
        identifier,
        {},
        {"id": identifier, "chatType": kind, "memberIds": sorted(map(str, ids))},
        now,
    )
    return value


async def change_member(session, actor, state, conversation, operation, ids, payload, now):
    action = operation.rsplit(".", 1)[-1]
    if conversation.kind == "channel" and conversation.payload.get("membershipType") not in {
        "private",
        "shared",
    }:
        bad("Only private and shared channels have independent membership")
    if conversation.kind == "chat" and conversation.payload.get("chatType") != "group":
        bad("Membership changes require a group chat")
    old, before = None, {}
    if action == "add":
        user, roles = await member_input(session, actor.tenant_id, payload, conversation.kind)
        if (
            conversation.kind == "channel"
            and conversation.payload.get("membershipType") == "private"
        ):
            team = await record_at(session, actor.tenant_id, state.scope, "team", state.scope)
            if not await authority(session, actor, team, user=user.id):
                bad("Private-channel members must be current team members")
    else:
        old = await child_at(session, actor.tenant_id, conversation, "members", ids["member"])
        before = copy.deepcopy(old.payload)
        user = await local_user(session, actor.tenant_id, before["userId"])
        if action == "update":
            fields(payload, {"roles", "@odata.type"}, {"roles"})
            if (
                payload.get("@odata.type", "#microsoft.graph.aadUserConversationMember")
                != "#microsoft.graph.aadUserConversationMember"
            ):
                bad("Unsupported member type")
            roles = roles_input(payload["roles"])
            if user.profile.get("userType") == "Guest" or "guest" in roles:
                bad("Guest membership role cannot be changed")
        else:
            fields(payload, set())
            roles = []
    await require_owner_change(
        session,
        actor,
        conversation,
        roles,
        before.get("roles", []),
        self_delete=action == "delete" and actor.subject_id == user.id,
    )
    state.revision += 1
    if action == "add":
        result = await add_member(session, actor, state, conversation, user, roles)
    elif action == "update":
        if "owner" in before.get("roles", []) and "owner" not in roles:
            other = await session.scalar(
                select(A.id)
                .where(
                    A.tenant_id == actor.tenant_id,
                    A.scope == state.scope,
                    A.resource_kind == conversation.kind,
                    A.resource_id == conversation.payload["id"],
                    A.permission == "owner",
                    A.principal_id != user.id,
                )
                .limit(1)
            )
            if not other:
                bad("A conversation must retain an authoritative owner")
        result = {**before, "roles": roles}
        await write_record(session, state, old.kind, result, previous=old)
        await set_membership(session, state, conversation, user.id, roles)
    else:
        await remove_member(session, state, conversation, old)
        # Removing a team member revokes its inherited and private-channel authority.
        if conversation.kind == "team":
            private_channels = select(V).where(
                V.tenant_id == actor.tenant_id,
                V.workload == "teams",
                V.scope == state.scope,
                V.kind == "channel",
                V.payload["membershipType"].as_string() == "private",
                V.valid_to.is_(None),
                V.deleted.is_(False),
            )
            for channel in await session.scalars(private_channels):
                membership = await member_for_user(session, actor.tenant_id, channel, user.id)
                if membership is not None:
                    await remove_member(session, state, channel, membership)
                else:
                    await set_membership(session, state, channel, user.id, [], remove=True)
        result = None
    await record_permission_fact(session, actor, operation, state.scope, before, result or {}, now)
    return result


async def message_references(session, actor, conversation, payload):
    for mention in payload.get("mentions") or []:
        identity = mention.get("mentioned", {})
        if set(identity) != {"user"} or "id" not in identity["user"]:
            bad("Only local user mentions are supported")
        user = await local_user(session, actor.tenant_id, identity["user"]["id"])
        if not await authority(session, actor, conversation, user=user.id):
            bad("Mentioned user is not a current conversation member")
    for attachment in payload.get("attachments") or []:
        if attachment.get("contentType") != "reference" or not attachment.get("contentUrl"):
            bad("Only local drive reference attachments are supported")
        row = await session.scalar(
            select(V)
            .where(
                V.tenant_id == actor.tenant_id,
                V.workload == "sharepoint",
                V.kind == "driveItem",
                V.payload["webUrl"].as_string() == attachment["contentUrl"],
                V.valid_to.is_(None),
                V.deleted.is_(False),
            )
            .limit(1)
        )
        if row is None:
            bad("Reference attachment must identify a stored local drive item")


async def change_message(session, actor, state, conversation, operation, ids, payload, now):
    action = operation.rsplit(".", 1)[-1]
    parent = conversation
    relation = "messages"
    if operation.startswith("teams.reply."):
        parent = await child_at(session, actor.tenant_id, conversation, "messages", ids["message"])
        if parent.payload.get("deletedDateTime"):
            bad("Cannot change a reply under a deleted root message")
        relation = "replies"
    old = None
    before = {}
    if action != "send":
        old = await child_at(session, actor.tenant_id, parent, relation, ids["item"])
        before = copy.deepcopy(old.payload)
        if before.get("from", {}).get("user", {}).get("id") != str(actor.subject_id):
            denied("Only the current message author can edit, delete or restore it")
    if action in {"send", "update"}:
        message_input(payload, update=action == "update", channel=conversation.kind == "channel")
        await message_references(session, actor, conversation, payload)
        if before.get("deletedDateTime"):
            bad("Restore the message before editing it")
    else:
        fields(payload, set())
    state.revision += 1
    if action == "send":
        author = await local_user(session, actor.tenant_id, str(actor.subject_id))
        result = {
            "id": str(int(now.timestamp() * 1000)) + str(uuid4().int % 1000000).zfill(6),
            "body": payload["body"],
            "messageType": "message",
            "importance": "normal",
            "locale": "en-US",
            "createdDateTime": now.isoformat(),
            "lastModifiedDateTime": now.isoformat(),
            "deletedDateTime": None,
            "from": {
                "user": {
                    "@odata.type": "#microsoft.graph.teamworkUserIdentity",
                    "id": str(author.id),
                    "displayName": author.display_name,
                    "userIdentityType": "aadUser",
                }
            },
            "attachments": [],
            "mentions": [],
            **copy.deepcopy(payload),
        }
        if relation == "replies":
            result["replyToId"] = parent.payload["id"]
        if conversation.kind == "chat":
            result["chatId"] = conversation.payload["id"]
        else:
            result["channelIdentity"] = {
                "teamId": state.scope,
                "channelId": conversation.payload["id"],
            }
        created = await write_record(session, state, "chatMessage", result)
        add_edge(session, state, parent, relation, created)
    elif action == "update":
        result = {
            **before,
            **copy.deepcopy(payload),
            "lastEditedDateTime": now.isoformat(),
            "lastModifiedDateTime": now.isoformat(),
        }
        await write_record(session, state, "chatMessage", result, previous=old)
    elif action == "softdelete":
        if before.get("deletedDateTime"):
            bad("Message is already deleted")
        result = {
            **before,
            "deletedDateTime": now.isoformat(),
            "lastModifiedDateTime": now.isoformat(),
            "body": {"contentType": "text", "content": ""},
            "attachments": [],
            "mentions": [],
        }
        await write_record(session, state, "chatMessage", result, previous=old)
    elif action == "undosoftdelete":
        if not before.get("deletedDateTime"):
            bad("Message is not deleted")
        previous = await session.scalar(
            select(V)
            .where(
                V.tenant_id == actor.tenant_id,
                V.workload == "teams",
                V.scope == state.scope,
                V.kind == "chatMessage",
                V.id == old.id,
                V.valid_from < old.valid_from,
                V.payload["deletedDateTime"].as_string().is_(None),
                V.deleted.is_(False),
            )
            .order_by(V.valid_from.desc())
            .limit(1)
        )
        if previous is None:
            raise GraphError(409, "Conflict", "No recoverable local message revision exists")
        result = {
            **copy.deepcopy(previous.payload),
            "deletedDateTime": None,
            "lastModifiedDateTime": now.isoformat(),
        }
        await write_record(session, state, "chatMessage", result, previous=old)
    else:
        bad("Unsupported message operation")
    await record_permission_fact(
        session,
        actor,
        operation,
        state.scope,
        {"id": before["id"]} if before else {},
        {
            "id": result["id"],
            "replyToId": result.get("replyToId"),
            "deleted": bool(result.get("deletedDateTime")),
        },
        now,
    )
    return result if action == "send" else None


async def apply_team_operation(
    session, actor, operation_key, scope_id, path_ids, payload, wall_now
):
    # Shared evidence lock precedes workload locks, matching existing native fact writers.
    await lock_state(session, actor.tenant_id)
    if operation_key == "teams.chat.create":
        return await create_chat(session, actor, payload, wall_now)
    category = operation_key.split(".")[1]
    state, conversation = await conversation_for(session, actor, category, path_ids)
    if state.scope != scope_id:
        bad("Conversation scope mismatch")
    await require_write(session, actor, operation_key, conversation)
    if "user" in path_ids and str(actor.subject_id) != path_ids["user"]:
        denied("The user-scoped message action must identify the delegated caller")
    if ".member." in operation_key:
        return await change_member(
            session, actor, state, conversation, operation_key, path_ids, payload, wall_now
        )
    return await change_message(
        session, actor, state, conversation, operation_key, path_ids, payload, wall_now
    )

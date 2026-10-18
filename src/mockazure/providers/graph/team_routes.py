"""Graph v1.0 Teams evidence collection, with no remote content fetching."""

import copy

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from starlette.background import BackgroundTask

from mockazure.content.repository import object_row, stream_content
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import principal
from mockazure.workloads.cursors import binding
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.queries import invalid, project
from mockazure.workloads.repository import current_record, scope_row
from mockazure.workloads.team_access import authorize_row, visibility
from mockazure.workloads.team_queries import children, collection, options, page_state

router = APIRouter(prefix="/v1.0")


def missing():
    raise GraphError(404, "NotFound", "Teams evidence was not found")


async def child(session, actor, scope, source, relation, identifier):
    state = await scope_row(session, actor.tenant_id, "teams", scope, lock="read")
    query = children(actor, scope, source, relation, state.revision).where(
        V.payload["id"].as_string() == identifier
    )
    row = await session.scalar(query)
    if row is None:
        missing()
    return row


async def list_children(request, session, actor, conversation, source, relation, kind, profile):
    state, token = await page_state(request, session, actor, conversation.scope, profile, kind)
    revision = state["scopes"][conversation.scope]["upper"]
    query = children(actor, conversation.scope, source, relation, revision)
    if relation == "channels":
        query = query.where(visibility(actor))

    async def expanded(row):
        value = project(row, state["options"]["select"])
        if state["options"]["expand"] == "replies":
            resource = request.scope["path"].rstrip("/") + "/" + row.payload["id"] + "/replies"
            nested = copy.deepcopy(state)
            nested.update(binding=binding(actor, resource), offset=0)
            nested["options"] = {
                "top": 200,
                "select": None,
                "expand": None,
                "order": None,
                "filters": [],
            }
            result = await collection(
                request,
                session,
                actor,
                children(actor, conversation.scope, row, "replies", revision),
                nested,
                None,
                "chatMessage",
                "replies",
                conversation.scope,
                resource=resource,
            )
            value["replies"] = result["value"]
            if "@odata.nextLink" in result:
                value["replies@odata.nextLink"] = result["@odata.nextLink"]
        return value

    return await collection(
        request,
        session,
        actor,
        query,
        state,
        token,
        kind,
        profile,
        conversation.scope,
        render=expanded,
    )


async def hosted_bytes(session, actor, row):
    if row.content_id is None:
        missing()
    blob = await object_row(session, actor.tenant_id, row.content_id, lock="read", finalized=True)

    async def body():
        try:
            async for chunk in stream_content(session, actor.tenant_id, row.content_id):
                yield chunk
        finally:
            await session.close()

    return StreamingResponse(
        body(),
        media_type=blob.media_type,
        headers={"Content-Length": str(blob.size)},
        background=BackgroundTask(session.close),
    )


async def dispatch(request, session, actor, kind, path):
    parts = path.split("/")
    if any(part in {"", ".", ".."} or "\\" in part for part in parts):
        invalid("Invalid Teams resource path")
    root = await current_record(session, actor.tenant_id, "teams", kind, parts[0])
    if root is None:
        missing()
    tail = parts[1:]
    conversation = root
    if kind == "team" and tail and tail[0] == "channels":
        if len(tail) == 1:
            await authorize_row(session, actor, root, "channel")
            return await list_children(
                request, session, actor, root, root, "channels", "channel", "channels"
            )
        conversation = await child(session, actor, root.scope, root, "channels", tail[1])
        tail = tail[2:]
    category = "chat" if kind == "chat" else "channel" if conversation.kind == "channel" else "team"
    if not tail:
        await authorize_row(session, actor, conversation, category)
        opts = options(
            request, "team" if category in {"team", "channel"} else "item", conversation.kind
        )
        result = project(conversation, opts["select"])
        if category == "channel" and (opts["select"] is None or "summary" not in opts["select"]):
            result.pop("summary", None)
        return result
    if tail == ["members"]:
        await authorize_row(session, actor, conversation, category + "Members")
        return await list_children(
            request,
            session,
            actor,
            conversation,
            conversation,
            "members",
            "conversationMember",
            "item" if kind == "chat" else "members",
        )
    if tail == ["filesFolder"] and category == "channel":
        from mockazure.workloads.drives import resolve_drive_item
        from mockazure.workloads.resource_access import grant_exists

        options(request, "item", "driveItem")
        await authorize_row(session, actor, conversation, "filesFolder")
        state = await scope_row(session, actor.tenant_id, "teams", root.scope, lock="read")
        reference = await session.scalar(
            children(actor, root.scope, conversation, "filesFolder", state.revision)
        )
        if reference is None:
            missing()
        folder = await resolve_drive_item(
            session, actor.tenant_id, reference.payload["driveId"], reference.payload["itemId"]
        )
        await scope_row(session, actor.tenant_id, "sharepoint", folder["drive"].scope, lock="read")
        if actor.token_kind == "delegated" and not await session.scalar(
            select(grant_exists(actor, folder["drive"].scope, "user", actor.subject_id))
        ):
            raise GraphError(403, "Forbidden", "Signed-in user requires local site access")
        if "folder" not in folder["row"].payload:
            missing()
        return copy.deepcopy(folder["row"].payload)
    if tail[0] != "messages" or conversation.kind == "team":
        raise GraphError(501, "NotSupported", "This Teams operation is not implemented")
    operation = category + ("Message" if kind == "chat" and len(tail) > 1 else "Messages")
    if "hostedContents" in tail[2::2]:
        operation = category + "Hosted"
    await authorize_row(session, actor, conversation, operation)
    if len(tail) == 1:
        return await list_children(
            request,
            session,
            actor,
            conversation,
            conversation,
            "messages",
            "chatMessage",
            category + "Messages",
        )
    message = await child(session, actor, root.scope, conversation, "messages", tail[1])
    tail = tail[2:]
    if tail and tail[0] == "replies" and category == "channel":
        if len(tail) == 1:
            return await list_children(
                request, session, actor, conversation, message, "replies", "chatMessage", "replies"
            )
        message = await child(session, actor, root.scope, message, "replies", tail[1])
        tail = tail[2:]
    if not tail:
        options(request, "item", "chatMessage")
        return copy.deepcopy(message.payload)
    if tail[0] == "hostedContents":
        if len(tail) == 1:
            return await list_children(
                request,
                session,
                actor,
                conversation,
                message,
                "hostedContents",
                "chatMessageHostedContent",
                "item",
            )
        hosted = await child(session, actor, root.scope, message, "hostedContents", tail[1])
        options(request, "item", "chatMessageHostedContent")
        if len(tail) == 2:
            return copy.deepcopy(hosted.payload)
        if tail[2:] == ["$value"]:
            return await hosted_bytes(session, actor, hosted)
    raise GraphError(501, "NotSupported", "This Teams operation is not implemented")


async def endpoint(request, kind, path):
    session = request.app.state.database.sessions()
    try:
        await session.begin()
        actor = await principal(request, session)
        result = await dispatch(request, session, actor, kind, path)
        if isinstance(result, StreamingResponse):
            return result
        await session.commit()
        await session.close()
        return result
    except BaseException:
        await session.close()
        raise


@router.get("/teams/{team_path:path}")
async def team_endpoint(team_path: str, request: Request):
    return await endpoint(request, "team", team_path)


@router.get("/chats/{chat_path:path}")
async def chat_endpoint(chat_path: str, request: Request):
    return await endpoint(request, "chat", chat_path)


@router.get("/chats")
@router.get("/me/chats")
@router.get("/users/{user}/chats")
async def chats(request: Request, user: str = "me"):
    from mockazure.workloads.team_discovery import discover

    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        return await discover(request, session, actor, user, "chat")


@router.get("/me/joinedTeams")
@router.get("/users/{user}/joinedTeams")
async def joined_teams(request: Request, user: str = "me"):
    from mockazure.workloads.team_discovery import discover

    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        return await discover(request, session, actor, user, "team")


@router.get("/me/chats/{chat_path:path}")
@router.get("/users/{user}/chats/{chat_path:path}")
async def user_chat(chat_path: str, request: Request, user: str = "me"):
    from mockazure.workloads.team_access import member_exists
    from mockazure.workloads.team_discovery import user_id

    session = request.app.state.database.sessions()
    try:
        await session.begin()
        actor = await principal(request, session)
        identifier = await user_id(session, actor, user)
        chat_id = chat_path.split("/", 1)[0]
        if not await session.scalar(
            select(member_exists(actor, "chat", chat_id, chat_id, identifier))
        ):
            missing()
        result = await dispatch(request, session, actor, "chat", chat_path)
        if isinstance(result, StreamingResponse):
            return result
        await session.commit()
        await session.close()
        return result
    except BaseException:
        await session.close()
        raise

from datetime import datetime, timedelta

from mockazure.population.generators.business import edge, linked
from mockazure.population.types import Mutation


def item(g, kind, i):
    per_project = (
        3
        if kind in {"channels", "channel-members", "files-folders"}
        else (
            2
            if kind
            in {
                "team-members",
                "chat-members",
                "channel-messages",
                "channel-replies",
                "chat-messages",
            }
            else 1
        )
    )
    if kind in {"team-members", "chat-members"}:
        per_project = min(g.users, 2)
    if g.count > g.projects * per_project:
        raise ValueError("Teams fixture count exceeds project inventory")
    project = i // per_project
    team, chat = g.id("team", project), g.id("chat", project)
    channel = g.id("channel", i if per_project == 3 else project * 3)
    if kind in {"teams", "chats"}:
        identifier = team if kind == "teams" else chat
        payload = (
            {"id": team, "displayName": f"Project {project + 1}", "visibility": "private"}
            if kind == "teams"
            else {
                "id": chat,
                "topic": f"Project {project + 1} planning",
                "chatType": "group",
                "createdDateTime": g.time(),
                "lastUpdatedDateTime": g.time(),
            }
        )
        return Mutation(
            operation="import",
            logical_key=f"business.{kind}:{i}",
            parent=identifier,
            payload=payload,
        )
    if kind == "channels":
        return linked(
            kind,
            i,
            team,
            {
                "id": channel,
                "displayName": ("General", "Leadership", "Partners")[i % 3],
                "membershipType": ("standard", "private", "shared")[i % 3],
                "createdDateTime": g.time(),
            },
            [edge("team", team, "channels", "channel", channel)],
        )
    if kind.endswith("members"):
        root_kind = {"team-members": "team", "channel-members": "channel", "chat-members": "chat"}[
            kind
        ]
        root = {"team": team, "channel": channel, "chat": chat}[root_kind]
        user = g.person(project + (i % per_project if per_project == 2 else 0))
        identifier = g.id(kind, i)
        return linked(
            kind,
            i,
            chat if root_kind == "chat" else team,
            {
                "id": identifier,
                "@odata.type": "#microsoft.graph.aadUserConversationMember",
                "userId": user["id"],
                "displayName": user["displayName"],
                "email": user["userPrincipalName"],
                "tenantId": str(g.tenant),
                "roles": ["owner"] if i % per_project == 0 else [],
            },
            [edge(root_kind, root, "members", "conversationMember", identifier)],
        )
    if kind == "files-folders":
        identifier = g.id("files-folder", i)
        return linked(
            kind,
            i,
            team,
            {
                "id": identifier,
                "driveId": g.id("drive", project),
                "itemId": g.id("folder", project),
            },
            [edge("channel", channel, "filesFolder", "filesFolderReference", identifier)],
        )
    is_chat, reply = kind == "chat-messages", kind == "channel-replies"
    identifier = g.id(kind, i)
    sender = g.person(project + i % 2)
    target = g.person(project + 1 - i % 2)
    payload = {
        "id": identifier,
        "messageType": "message",
        "createdDateTime": g.time(i),
        "lastModifiedDateTime": g.time(i),
        "importance": "normal",
        "locale": "en-US",
        "from": {
            "user": {
                "id": sender["id"],
                "displayName": sender["displayName"],
                "userIdentityType": "aadUser",
                "@odata.type": "#microsoft.graph.teamworkUserIdentity",
            }
        },
        "body": {
            "contentType": "html",
            "content": '<at id="0">' + target["displayName"] + "</at> please review the document.",
        },
        "mentions": [
            {
                "id": 0,
                "mentionText": target["displayName"],
                "mentioned": {"user": {"id": target["id"], "displayName": target["displayName"]}},
            }
        ],
        "attachments": [
            {
                "id": g.id("document", project),
                "contentType": "reference",
                "contentUrl": f"https://{g.domain}/sites/project-{project}/documents/review",
                "name": f"Project {project + 1} review",
            }
        ],
    }
    if reply:
        payload["replyToId"] = g.id("channel-messages", i)
    elif i % 2:
        changed = (
            datetime.fromisoformat(payload["createdDateTime"].replace("Z", "+00:00"))
            + timedelta(seconds=30)
        ).isoformat()
        payload["lastModifiedDateTime"] = changed
        if is_chat:
            payload["deletedDateTime"] = changed
            payload["body"] = {"contentType": "text", "content": ""}
            payload["mentions"], payload["attachments"] = [], []
        else:
            payload["lastEditedDateTime"] = changed
            payload["body"]["content"] += " Updated review deadline."
    relation = (
        edge("chatMessage", payload["replyToId"], "replies", "chatMessage", identifier)
        if reply
        else edge(
            "chat" if is_chat else "channel",
            chat if is_chat else channel,
            "messages",
            "chatMessage",
            identifier,
        )
    )
    return linked(kind, i, chat if is_chat else team, payload, [relation])

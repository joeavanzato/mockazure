from urllib.parse import quote


def message(identifier, text="evidence"):
    return {
        "id": identifier,
        "body": {"contentType": "text", "content": text},
        "importance": "normal",
        "locale": "en-us",
        "messageType": "message",
        "createdDateTime": "2026-10-07T00:00:00Z",
        "lastModifiedDateTime": "2026-10-07T00:00:00Z",
    }


def record(kind, identifier, payload):
    return {"kind": kind, "id": identifier, "payload": {"id": identifier, **payload}}


def edge(source_kind, source, relation, target_kind, target):
    return {
        "source": {"kind": source_kind, "id": source},
        "relation": relation,
        "target": {"kind": target_kind, "id": target},
    }


async def seed_team(client, tenant, suffix="one", membership="standard", count=3):
    team, channel = "team-" + suffix, "19:channel#" + suffix + "@thread.tacv2"
    records = [
        record("team", team, {"displayName": "Evidence " + suffix}),
        record("channel", channel, {"displayName": "Evidence", "membershipType": membership}),
    ]
    edges = [edge("team", team, "channels", "channel", channel)]
    for i in range(count):
        identifier = f"message#{i}"
        records.append(record("chatMessage", identifier, message(identifier)))
        edges.append(edge("channel", channel, "messages", "chatMessage", identifier))
    result = await client.post(
        f"/_mock/tenants/{tenant}/workloads/teams/import",
        json={"scope": team, "records": records, "edges": edges},
    )
    assert result.status_code == 200, result.text
    return {
        "team": team,
        "channel": channel,
        "base": f"/v1.0/teams/{team}/channels/{quote(channel, safe='')}",
    }


async def access(
    client, tenant, scope, kind, identifier, principal, permission="member", principal_kind="user"
):
    response = await client.post(
        f"/_mock/tenants/{tenant}/workloads/teams/access",
        json={
            "scope": scope,
            "resource_kind": kind,
            "resource_id": identifier,
            "principal_kind": principal_kind,
            "principal_id": str(principal),
            "permission": permission,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def seed_chat(client, tenant, suffix="one", count=3):
    chat = "19:chat#" + suffix + "@thread.v2"
    records = [record("chat", chat, {"chatType": "group", "topic": "Local evidence"})]
    edges = []
    for i in range(count):
        identifier = f"message#{i}"
        records.append(record("chatMessage", identifier, message(identifier)))
        edges.append(edge("chat", chat, "messages", "chatMessage", identifier))
    response = await client.post(
        f"/_mock/tenants/{tenant}/workloads/teams/import",
        json={"scope": chat, "records": records, "edges": edges},
    )
    assert response.status_code == 200, response.text
    return {"chat": chat, "base": "/v1.0/chats/" + quote(chat, safe="")}

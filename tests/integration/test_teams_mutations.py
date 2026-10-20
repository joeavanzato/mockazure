from urllib.parse import quote

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.teams_support import seed_team


def member_body(user, roles=None):
    return {
        "@odata.type": "#microsoft.graph.aadUserConversationMember",
        "roles": roles or [],
        "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{user}')",
    }


async def add_user(client, tenant, name="second"):
    response = await client.post(
        f"/_mock/tenants/{tenant}/identity/users",
        json={
            "upn": name + "@contoso.test",
            "display_name": name,
            "password": "local-test-password",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def team_member(client, tenant, app_headers, team, user, roles=None):
    response = await client.post(
        f"/v1.0/teams/{team}/members", headers=app_headers, json=member_body(user, roles)
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_member_change_changes_current_visibility(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_team(client, tenant)
        write = await headers_for(
            database, settings, tenant, app, None, ["TeamMember.ReadWrite.All"]
        )
        read = await headers_for(
            database, settings, tenant, app, user, ["ChannelMessage.Read.All"], kind="delegated"
        )
        path = fixture["base"] + "/messages"
        assert (await client.get(path, headers=read)).status_code == 403
        membership = await team_member(client, tenant, write, fixture["team"], user)
        assert (await client.get(path, headers=read)).status_code == 200
        member_path = f"/v1.0/teams/{fixture['team']}/members/{quote(membership, safe='')}"
        found = await client.get(member_path, headers=write)
        assert found.status_code == 200 and found.json()["userId"] == str(user)
        promoted = await client.patch(member_path, headers=write, json={"roles": ["owner"]})
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["roles"] == ["owner"]
        second = await add_user(client, tenant)
        await team_member(client, tenant, write, fixture["team"], second, ["owner"])
        removed = await client.delete(member_path, headers=write)
        assert removed.status_code == 204, removed.text
        assert (await client.get(path, headers=read)).status_code == 403
        assert (await client.get(member_path, headers=write)).status_code == 404


async def test_message_author_and_edit_rules_and_thread_identity(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_team(client, tenant, count=0)
        second = await add_user(client, tenant)
        admin = await headers_for(
            database, settings, tenant, app, None, ["TeamMember.ReadWrite.All"]
        )
        await team_member(client, tenant, admin, fixture["team"], user, ["owner"])
        await team_member(client, tenant, admin, fixture["team"], second)
        permissions = ["ChannelMessage.Send", "ChannelMessage.ReadWrite", "ChannelMessage.Read.All"]
        author = await headers_for(
            database, settings, tenant, app, user, permissions, kind="delegated"
        )
        other = await headers_for(
            database, settings, tenant, app, second, permissions, kind="delegated"
        )
        sent = await client.post(
            fixture["base"] + "/messages",
            headers=author,
            json={"body": {"contentType": "text", "content": "Initial"}},
        )
        assert sent.status_code == 201, sent.text
        assert sent.json()["from"]["user"]["id"] == str(user)
        path = fixture["base"] + "/messages/" + sent.json()["id"]
        reply = await client.post(
            path + "/replies",
            headers=other,
            json={"body": {"contentType": "text", "content": "Reply"}},
        )
        assert reply.status_code == 201, reply.text
        assert reply.json()["replyToId"] == sent.json()["id"]
        changed = {"body": {"contentType": "text", "content": "Edited"}}
        assert (await client.patch(path, headers=other, json=changed)).status_code == 403
        assert (await client.patch(path, headers=author, json=changed)).status_code == 204
        assert (await client.get(path, headers=author)).json()["body"]["content"] == "Edited"
        assert (await client.post(path + "/softDelete", headers=author)).status_code == 204
        deleted = await client.get(path, headers=author)
        assert deleted.json()["deletedDateTime"] and deleted.json()["body"]["content"] == ""
        assert (await client.post(path + "/undoSoftDelete", headers=author)).status_code == 204
        restored = (await client.get(path, headers=author)).json()
        assert restored["body"]["content"] == "Edited"
        assert restored["id"] == sent.json()["id"]
        replies = (await client.get(path + "/replies", headers=author)).json()["value"]
        assert [r["id"] for r in replies] == [reply.json()["id"]]


async def test_chat_creation_one_on_one_reuse_and_documented_soft_delete_path(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        second = await add_user(client, tenant)
        permissions = ["Chat.Create", "Chat.ReadWrite", "ChatMessage.Send", "ChatMember.ReadWrite"]
        actor = await headers_for(
            database, settings, tenant, app, user, permissions, kind="delegated"
        )
        body = {
            "chatType": "oneOnOne",
            "members": [
                member_body(user, ["owner"]),
                member_body(second, ["owner"]),
            ],
        }
        response = await client.post("/v1.0/chats", headers=actor, json=body)
        assert response.status_code == 201, response.text
        chat = response.json()["id"]
        replay = await client.post("/v1.0/chats", headers=actor, json=body)
        assert replay.status_code == 201 and replay.json()["id"] == chat
        root = "/v1.0/chats/" + quote(chat, safe="")
        sent = await client.post(
            root + "/messages",
            headers=actor,
            json={"body": {"contentType": "text", "content": "Chat"}},
        )
        assert sent.status_code == 201, sent.text
        message = sent.json()["id"]
        scoped = f"/v1.0/users/{user}/chats/{quote(chat, safe='')}/messages/{message}"
        assert (await client.post(scoped + "/softDelete", headers=actor)).status_code == 204
        assert (await client.post(scoped + "/undoSoftDelete", headers=actor)).status_code == 204
        assert (await client.get(root + "/messages/" + message, headers=actor)).json()["body"][
            "content"
        ] == "Chat"

from urllib.parse import quote

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.teams_support import access, edge, message, record, seed_chat, seed_team


async def test_chat_preview_order_uses_snapshot_message_creation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        old = await seed_chat(client, tenant, suffix="a-old", count=1)
        new = await seed_chat(client, tenant, suffix="z-new", count=1)
        for fixture in (old, new):
            await access(client, tenant, fixture["chat"], "chat", fixture["chat"], user)
        newest = message("message#0")
        newest["createdDateTime"] = "2026-10-09T00:00:00Z"
        result = await client.post(
            f"/_mock/tenants/{tenant}/workloads/teams/import",
            json={"scope": new["chat"], "records": [record("chatMessage", "message#0", newest)]},
        )
        assert result.status_code == 200, result.text
        auth = await headers_for(
            database, settings, tenant, app, user, {"Chat.ReadBasic"}, kind="delegated"
        )
        first = await client.get(
            "/v1.0/chats?$top=1&$orderby=lastMessagePreview/createdDateTime%20desc", headers=auth
        )
        assert first.status_code == 200, first.text
        assert first.json()["value"][0]["id"] == new["chat"]
        newest["createdDateTime"] = "2026-10-05T00:00:00Z"
        result = await client.post(
            f"/_mock/tenants/{tenant}/workloads/teams/import",
            json={"scope": new["chat"], "records": [record("chatMessage", "message#0", newest)]},
        )
        assert result.status_code == 200, result.text
        resumed = await client.get(first.json()["@odata.nextLink"], headers=auth)
        assert resumed.status_code == 200, resumed.text
        assert [row["id"] for row in resumed.json()["value"]] == [old["chat"]]


async def test_reset_without_restoration_invalidates_chat_channel_and_reply_links(
    database, settings
):
    async with graph_client(database, settings) as (client, tenant, app, user):
        team = await seed_team(client, tenant)
        chat = await seed_chat(client, tenant)
        result = await client.post(
            f"/_mock/tenants/{tenant}/workloads/teams/import",
            json={
                "scope": team["team"],
                "records": [
                    record("chatMessage", "reply" + str(i), message("reply" + str(i)))
                    for i in range(3)
                ],
                "edges": [
                    edge("chatMessage", "message#0", "replies", "chatMessage", "reply" + str(i))
                    for i in range(3)
                ],
            },
        )
        assert result.status_code == 200, result.text
        auth = await headers_for(
            database, settings, tenant, app, None, {"Chat.Read.All", "ChannelMessage.Read.All"}
        )
        paths = [
            chat["base"] + "/messages",
            team["base"] + "/messages",
            team["base"] + "/messages/" + quote("message#0", safe="") + "/replies",
        ]
        links = []
        for path in paths:
            page = await client.get(path + "?$top=1", headers=auth)
            assert page.status_code == 200, page.text
            links.append(page.json()["@odata.nextLink"])
        for scope, revision in ((team["team"], 2), (chat["chat"], 1)):
            reset = await client.post(
                f"/_mock/tenants/{tenant}/workloads/teams/reset",
                json={"scope": scope, "expected_revision": revision},
            )
            assert reset.status_code == 200, reset.text
        for link in links:
            result = await client.get(link, headers=auth)
            assert result.status_code == 410, result.text
            assert result.json()["error"]["code"] == "resyncRequired"

"""Native collaboration state, generated SDK wire shapes and rejection boundaries."""

import pytest

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.teams_support import seed_team
from tests.integration.test_teams_mutations import add_user, member_body, team_member


async def test_generated_graph_sdk_member_message_reply_and_delete(database, settings):
    from msgraph.generated.models.aad_user_conversation_member import AadUserConversationMember
    from msgraph.generated.models.body_type import BodyType
    from msgraph.generated.models.chat_message import ChatMessage
    from msgraph.generated.models.item_body import ItemBody

    from integrations.python.graph_directory import graph_client as sdk_client

    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_team(client, tenant, count=0)
        grants = [
            "TeamMember.ReadWrite.All",
            "ChannelMessage.Read.All",
            "ChannelMessage.Send",
            "ChannelMessage.ReadWrite",
        ]
        application = await headers_for(database, settings, tenant, app, None, grants)
        delegated = await headers_for(
            database, settings, tenant, app, user, grants, kind="delegated"
        )
        config = {"endpoints": {"graph": str(client.base_url).rstrip("/")}}
        admin = sdk_client(config, application["Authorization"][7:], client)
        member = await admin.teams.by_team_id(fixture["team"]).members.post(
            AadUserConversationMember(
                roles=["owner"],
                additional_data={
                    "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{user}')"
                },
            )
        )
        graph = sdk_client(config, delegated["Authorization"][7:], client)
        channel = graph.teams.by_team_id(fixture["team"]).channels.by_channel_id(fixture["channel"])
        sent = await channel.messages.post(
            ChatMessage(body=ItemBody(content_type=BodyType.Text, content="SDK native message"))
        )
        root = channel.messages.by_chat_message_id(sent.id)
        reply = await root.replies.post(
            ChatMessage(body=ItemBody(content_type=BodyType.Text, content="SDK native reply"))
        )
        assert reply.reply_to_id == sent.id
        assert sent.from_.user.id == str(user)
        assert (await root.get()).body.content == "SDK native message"
        await root.soft_delete.post()
        assert (await root.get()).deleted_date_time is not None
        await root.undo_soft_delete.post()
        assert (await root.get()).body.content == "SDK native message"
        second = await add_user(client, tenant)
        await team_member(client, tenant, application, fixture["team"], second, ["owner"])
        await (
            admin.teams.by_team_id(fixture["team"])
            .members.by_conversation_member_id(member.id)
            .delete()
        )
        assert (
            await client.get(fixture["base"] + "/messages", headers=delegated)
        ).status_code == 403


@pytest.mark.parametrize(
    "extra", [{"mentions": 2}, {"attachments": None}, {"importance": "invented"}]
)
async def test_malformed_message_is_a_graph_error(database, settings, extra):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_team(client, tenant, count=0)
        admin = await headers_for(
            database, settings, tenant, app, None, ["TeamMember.ReadWrite.All"]
        )
        await team_member(client, tenant, admin, fixture["team"], user)
        actor = await headers_for(
            database, settings, tenant, app, user, ["ChannelMessage.Send"], kind="delegated"
        )
        result = await client.post(
            fixture["base"] + "/messages",
            headers=actor,
            json={"body": {"contentType": "text", "content": "Bad"}, **extra},
        )
        assert result.status_code == 400, result.text


async def test_team_removal_removes_private_channel_roster_and_authority(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_team(client, tenant, membership="private", count=0)
        second = await add_user(client, tenant)
        headers = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            ["TeamMember.ReadWrite.All", "ChannelMember.ReadWrite.All"],
        )
        member = await team_member(client, tenant, headers, fixture["team"], user)
        await team_member(client, tenant, headers, fixture["team"], second, ["owner"])
        for person, roles in ((user, []), (second, ["owner"])):
            added = await client.post(
                fixture["base"] + "/members", headers=headers, json=member_body(person, roles)
            )
            assert added.status_code == 201, added.text
        removed = await client.delete(
            f"/v1.0/teams/{fixture['team']}/members/{member}", headers=headers
        )
        assert removed.status_code == 204, removed.text
        roster = await client.get(fixture["base"] + "/members", headers=headers)
        assert {m["userId"] for m in roster.json()["value"]} == {str(second)}

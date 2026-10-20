from tests.integration.graph_support import graph_client, headers_for
from tests.integration.teams_support import access, seed_team
from tests.integration.test_teams_mutations import member_body, team_member


async def test_ordinary_send_requires_supported_authority(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_team(client, tenant)
        for permission in ("Teamwork.Migrate.All", "Chat.ReadWrite.All", "ChannelMessage.Read.All"):
            headers = await headers_for(database, settings, tenant, app, None, [permission])
            response = await client.post(
                fixture["base"] + "/messages",
                headers=headers,
                json={"body": {"contentType": "text", "content": "Denied"}},
            )
            assert response.status_code == 403, response.text
        actor = await headers_for(
            database, settings, tenant, app, user, ["ChannelMessage.Send"], kind="delegated"
        )
        denied = await client.post(
            fixture["base"] + "/messages",
            headers=actor,
            json={"body": {"contentType": "text", "content": "No membership"}},
        )
        assert denied.status_code == 403
        await access(client, tenant, fixture["team"], "team", fixture["team"], user)
        sent = await client.post(
            fixture["base"] + "/messages",
            headers=actor,
            json={"body": {"contentType": "text", "content": "Allowed"}},
        )
        assert sent.status_code == 201, sent.text
        forged = await client.post(
            fixture["base"] + "/messages",
            headers=actor,
            json={
                "body": {"contentType": "text", "content": "Forged"},
                "from": {"user": {"id": str(user)}},
            },
        )
        assert forged.status_code == 400


async def test_private_channel_membership_is_not_team_membership(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_team(client, tenant, membership="private")
        admin = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            [
                "TeamMember.ReadWrite.All",
                "ChannelMember.ReadWrite.All",
            ],
        )
        await team_member(client, tenant, admin, fixture["team"], user, ["owner"])
        actor = await headers_for(
            database, settings, tenant, app, user, ["ChannelMessage.Send"], kind="delegated"
        )
        body = {"body": {"contentType": "text", "content": "Private"}}
        assert (
            await client.post(fixture["base"] + "/messages", headers=actor, json=body)
        ).status_code == 403
        added = await client.post(
            fixture["base"] + "/members", headers=admin, json=member_body(user, ["owner"])
        )
        assert added.status_code == 201, added.text
        assert (
            await client.post(fixture["base"] + "/messages", headers=actor, json=body)
        ).status_code == 201


async def test_nonowner_permission_cannot_grant_owner_and_rsc_is_scope_bound(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        one = await seed_team(client, tenant, suffix="one", membership="shared")
        two = await seed_team(client, tenant, suffix="two", membership="shared")
        nonowner = await headers_for(
            database, settings, tenant, app, None, ["TeamMember.ReadWriteNonOwnerRole.All"]
        )
        result = await client.post(
            f"/v1.0/teams/{one['team']}/members",
            headers=nonowner,
            json=member_body(user, ["owner"]),
        )
        assert result.status_code == 403
        await access(
            client,
            tenant,
            one["team"],
            "team",
            one["team"],
            app,
            permission="ChannelMember.ReadWrite.Group",
            principal_kind="application",
        )
        actor = await headers_for(database, settings, tenant, app, None, [])
        added = await client.post(one["base"] + "/members", headers=actor, json=member_body(user))
        assert added.status_code == 201, added.text
        denied = await client.post(two["base"] + "/members", headers=actor, json=member_body(user))
        assert denied.status_code == 403

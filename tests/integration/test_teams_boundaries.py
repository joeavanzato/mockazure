from urllib.parse import quote

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.teams_support import access, edge, message, record, seed_chat, seed_team


async def test_imported_members_are_evidence_and_invalid_batches_are_atomic(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_team(client, tenant)
        member = record(
            "conversationMember",
            "member",
            {
                "@odata.type": "#microsoft.graph.aadUserConversationMember",
                "userId": str(user),
                "roles": ["owner"],
            },
        )
        response = await client.post(
            f"/_mock/tenants/{tenant}/workloads/teams/import",
            json={
                "scope": fixture["team"],
                "records": [member],
                "edges": [edge("team", fixture["team"], "members", "conversationMember", "member")],
            },
        )
        assert response.status_code == 200, response.text
        auth = await headers_for(
            database, settings, tenant, app, user, {"ChannelMessage.Read.All"}, kind="delegated"
        )
        assert (await client.get(fixture["base"] + "/messages", headers=auth)).status_code == 403
        response = await client.post(
            f"/_mock/tenants/{tenant}/workloads/teams/import",
            json={
                "scope": fixture["team"],
                "records": [record("chatMessage", "orphan", message("orphan"))],
            },
        )
        assert response.status_code == 422, response.text
        exported = await client.get(
            f"/_mock/tenants/{tenant}/workloads/teams/export?scope={fixture['team']}"
        )
        assert exported.json()["expected_revision"] == 2
        assert "orphan" not in {row["id"] for row in exported.json()["records"]}


async def test_chat_pages_survive_changes_and_pool_restart_but_reset_invalidates(
    database, settings
):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_chat(client, tenant)
        auth = await headers_for(database, settings, tenant, app, None, {"Chat.Read.All"})
        first = await client.get(fixture["base"] + "/messages?$top=1", headers=auth)
        assert first.status_code == 200, first.text
        link = first.json()["@odata.nextLink"]
        edited = message("message#1", "edited after snapshot")
        edited["lastModifiedDateTime"] = "2026-10-08T00:00:00Z"
        deleted = message("message#2", "")
        deleted["deletedDateTime"] = "2026-10-08T01:00:00Z"
        response = await client.post(
            f"/_mock/tenants/{tenant}/workloads/teams/import",
            json={
                "scope": fixture["chat"],
                "records": [
                    record("chatMessage", "message#1", edited),
                    record("chatMessage", "message#2", deleted),
                ],
            },
        )
        assert response.status_code == 200, response.text
        await database.engine.dispose()
        response = await client.get(link, headers=auth)
        assert response.status_code == 200, response.text
        assert response.json()["value"][0]["body"]["content"] == "evidence"
        current = await client.get(fixture["base"] + "/messages/message%231", headers=auth)
        assert current.json()["body"]["content"] == "edited after snapshot"
        response = await client.get(
            fixture["base"] + "/messages?$orderby=lastModifiedDateTime%20desc"
            "&$filter=lastModifiedDateTime%20gt%202026-10-07T12:00:00Z",
            headers=auth,
        )
        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()["value"]] == ["message#1"]
        export = (
            await client.get(
                f"/_mock/tenants/{tenant}/workloads/teams/export?scope="
                + quote(fixture["chat"], safe="")
            )
        ).json()
        response = await client.post(
            f"/_mock/tenants/{tenant}/workloads/teams/reset",
            json={"scope": fixture["chat"], "expected_revision": 2},
        )
        assert response.status_code == 200, response.text
        response = await client.post(
            f"/_mock/tenants/{tenant}/workloads/teams/import",
            json={**export, "expected_revision": 0},
        )
        assert response.status_code == 200, response.text
        assert (await client.get(link, headers=auth)).status_code == 410


async def test_teams_batch_charges_once_and_returns_retry_after(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_team(client, tenant)
        client._transport.app.state.settings.graph_collection_request_limit = 2
        auth = await headers_for(database, settings, tenant, app, None, {"Team.ReadBasic.All"})
        response = await client.post(
            "/v1.0/$batch",
            headers=auth,
            json={
                "requests": [
                    {"id": str(i), "method": "GET", "url": "/teams/" + fixture["team"]}
                    for i in range(3)
                ]
            },
        )
        assert response.status_code == 200, response.text
        assert [row["status"] for row in response.json()["responses"]] == [200, 200, 429]
        assert int(response.json()["responses"][2]["headers"]["retry-after"]) > 0


async def test_actual_local_issuer_roleless_application_uses_only_explicit_rsc(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_team(client, tenant)
        base = f"/_mock/tenants/{tenant}/identity"
        result = await client.post(
            base + "/applications", json={"display_name": "RSC only", "client_mode": "confidential"}
        )
        assert result.status_code == 201, result.text
        identifier = result.json()["client_id"]
        secret = (
            await client.post(
                base + f"/applications/{identifier}/credentials", json={"kind": "secret"}
            )
        ).json()["secret"]
        result = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": identifier,
                "client_secret": secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        assert result.status_code == 200, result.text
        auth = {"Authorization": "Bearer " + result.json()["access_token"]}
        assert (await client.get(fixture["base"] + "/messages", headers=auth)).status_code == 403
        await access(
            client,
            tenant,
            fixture["team"],
            "team",
            fixture["team"],
            identifier,
            "ChannelMessage.Read.Group",
            "application",
        )
        assert (await client.get(fixture["base"] + "/messages", headers=auth)).status_code == 200
        assert (await client.get("/v1.0/users", headers=auth)).status_code == 403


async def test_chat_user_alias_and_canonical_route_share_budget(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_chat(client, tenant)
        await access(client, tenant, fixture["chat"], "chat", fixture["chat"], user)
        client._transport.app.state.settings.graph_collection_request_limit = 1
        auth = await headers_for(database, settings, tenant, app, None, {"Chat.Read.All"})
        assert (await client.get(fixture["base"], headers=auth)).status_code == 200
        response = await client.get(
            f"/v1.0/users/{user}/chats/" + quote(fixture["chat"], safe=""), headers=auth
        )
        assert response.status_code == 429, response.text
        assert int(response.headers["retry-after"]) > 0


async def test_keyed_channel_uses_public_id_for_membership_and_fault_resume(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        team, channel = "keyed-team", "19:public-channel"
        response = await client.post(
            f"/_mock/tenants/{tenant}/workloads/teams/import",
            json={
                "scope": team,
                "records": [
                    record("team", team, {}),
                    {
                        **record(
                            "channel",
                            channel,
                            {"displayName": "Private", "membershipType": "private"},
                        ),
                        "key": "channel-storage",
                    },
                    *[record("chatMessage", str(i), message(str(i))) for i in range(2)],
                ],
                "edges": [
                    {
                        "source": {"kind": "team", "id": team},
                        "relation": "channels",
                        "target": {"kind": "channel", "id": channel, "key": "channel-storage"},
                    },
                    *[
                        {
                            "source": {"kind": "channel", "id": channel, "key": "channel-storage"},
                            "relation": "messages",
                            "target": {"kind": "chatMessage", "id": str(i)},
                        }
                        for i in range(2)
                    ],
                ],
            },
        )
        assert response.status_code == 200, response.text
        await access(client, tenant, team, "channel", channel, user)
        auth = await headers_for(
            database, settings, tenant, app, user, {"ChannelMessage.Read.All"}, kind="delegated"
        )
        base = f"/v1.0/teams/{team}/channels/" + quote(channel, safe="") + "/messages"
        first = await client.get(base + "?$top=1", headers=auth)
        assert first.status_code == 200, first.text
        fault = await client.post(
            f"/_mock/tenants/{tenant}/graph/faults",
            json={
                "client_id": str(app),
                "resource": "teams",
                "kind": "next_page_429",
                "retry_after": 3,
            },
        )
        assert fault.status_code == 201, fault.text
        response = await client.get(first.json()["@odata.nextLink"], headers=auth)
        assert response.status_code == 429 and response.headers["retry-after"] == "3"
        response = await client.get(first.json()["@odata.nextLink"], headers=auth)
        assert response.status_code == 200, response.text
        assert len(response.json()["value"]) == 1


async def test_channel_privacy_change_invalidates_existing_channel_pages(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_team(client, tenant, count=0)
        identifiers = [f"c{i:03}" for i in range(99)] + ["zz-last"]
        result = await client.post(
            f"/_mock/tenants/{tenant}/workloads/teams/import",
            json={
                "scope": fixture["team"],
                "records": [
                    record("channel", key, {"displayName": key, "membershipType": "standard"})
                    for key in identifiers
                ],
                "edges": [
                    edge("team", fixture["team"], "channels", "channel", key) for key in identifiers
                ],
            },
        )
        assert result.status_code == 200, result.text
        await access(client, tenant, fixture["team"], "team", fixture["team"], user)
        auth = await headers_for(
            database, settings, tenant, app, user, {"Channel.ReadBasic.All"}, kind="delegated"
        )
        first = await client.get(f"/v1.0/teams/{fixture['team']}/channels", headers=auth)
        assert first.status_code == 200 and len(first.json()["value"]) == 100, first.text
        result = await client.post(
            f"/_mock/tenants/{tenant}/workloads/teams/import",
            json={
                "scope": fixture["team"],
                "records": [
                    record(
                        "channel",
                        "zz-last",
                        {"displayName": "Now private", "membershipType": "private"},
                    )
                ],
            },
        )
        assert result.status_code == 200, result.text
        resumed = await client.get(first.json()["@odata.nextLink"], headers=auth)
        assert resumed.status_code == 403, resumed.text

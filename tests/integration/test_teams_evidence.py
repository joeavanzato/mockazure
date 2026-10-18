from tests.integration.graph_support import graph_client, headers_for
from tests.integration.teams_support import access, seed_team


async def test_channel_messages_preserve_opaque_ids_and_paged_snapshot(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_team(client, tenant)
        auth = await headers_for(database, settings, tenant, app, None, {"ChannelMessage.Read.All"})
        response = await client.get(fixture["base"] + "/messages?$top=1", headers=auth)
        assert response.status_code == 200, response.text
        seen = []
        while True:
            seen.extend(row["id"] for row in response.json()["value"])
            link = response.json().get("@odata.nextLink")
            if not link:
                break
            response = await client.get(link, headers=auth)
            assert response.status_code == 200, response.text
        assert set(seen) == {"message#0", "message#1", "message#2"}
        item = await client.get(fixture["base"] + "/messages/message%230", headers=auth)
        assert item.status_code == 200, item.text
        assert item.json()["body"]["content"] == "evidence"
        assert (
            await client.get(fixture["base"] + "/messages?$top=51", headers=auth)
        ).status_code == 400
        assert (
            await client.get(fixture["base"] + "/messages?$select=id", headers=auth)
        ).status_code == 400


async def test_private_and_shared_channels_require_explicit_membership(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        auth = await headers_for(
            database, settings, tenant, app, user, {"ChannelMessage.Read.All"}, kind="delegated"
        )
        for mode in ("private", "shared"):
            fixture = await seed_team(client, tenant, suffix=mode, membership=mode)
            await access(client, tenant, fixture["team"], "team", fixture["team"], user)
            assert (
                await client.get(fixture["base"] + "/messages", headers=auth)
            ).status_code == 403
            grant = await access(
                client, tenant, fixture["team"], "channel", fixture["channel"], user
            )
            first = await client.get(fixture["base"] + "/messages?$top=1", headers=auth)
            assert first.status_code == 200, first.text
            await client.delete(f"/_mock/tenants/{tenant}/workloads/teams/access/{grant}")
            assert (
                await client.get(first.json()["@odata.nextLink"], headers=auth)
            ).status_code == 403


async def test_resource_specific_consent_does_not_widen_to_other_teams(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        first = await seed_team(client, tenant)
        second = await seed_team(client, tenant, suffix="other")
        auth = await headers_for(database, settings, tenant, app, None, set())
        assert (await client.get(first["base"] + "/messages", headers=auth)).status_code == 403
        grant = await access(
            client,
            tenant,
            first["team"],
            "team",
            first["team"],
            app,
            permission="ChannelMessage.Read.Group",
            principal_kind="application",
        )
        assert (await client.get(first["base"] + "/messages", headers=auth)).status_code == 200
        assert (await client.get(second["base"] + "/messages", headers=auth)).status_code == 403
        await client.delete(f"/_mock/tenants/{tenant}/workloads/teams/access/{grant}")
        assert (await client.get(first["base"] + "/messages", headers=auth)).status_code == 403


async def test_discovery_uses_authoritative_memberships_and_self_aliases(database, settings):
    from tests.integration.teams_support import seed_chat

    async with graph_client(database, settings) as (client, tenant, app, user):
        team = await seed_team(client, tenant)
        first = await seed_chat(client, tenant)
        second = await seed_chat(client, tenant, suffix="other")
        await seed_chat(client, tenant, suffix="hidden")
        for kind, identifier in (
            ("team", team["team"]),
            ("chat", first["chat"]),
            ("chat", second["chat"]),
        ):
            await access(client, tenant, identifier, kind, identifier, user)
        delegated = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {"Team.ReadBasic.All", "Chat.ReadBasic"},
            kind="delegated",
        )
        for path in ("/v1.0/me/joinedTeams", f"/v1.0/users/{user}/joinedTeams"):
            response = await client.get(path, headers=delegated)
            assert response.status_code == 200, response.text
            assert [row["id"] for row in response.json()["value"]] == [team["team"]]
        seen = []
        response = await client.get("/v1.0/chats?$top=1", headers=delegated)
        while True:
            assert response.status_code == 200, response.text
            seen.extend(row["id"] for row in response.json()["value"])
            if "@odata.nextLink" not in response.json():
                break
            response = await client.get(response.json()["@odata.nextLink"], headers=delegated)
        assert set(seen) == {first["chat"], second["chat"]}
        app_auth = await headers_for(database, settings, tenant, app, None, {"Chat.ReadBasic.All"})
        assert (await client.get("/v1.0/chats", headers=app_auth)).status_code == 403
        response = await client.get(f"/v1.0/users/{user}/chats", headers=app_auth)
        assert response.status_code == 200, response.text
        assert len(response.json()["value"]) == 2


async def test_nested_replies_and_hosted_bytes_are_persistent_and_operation_scoped(
    database, settings
):
    from tests.integration.teams_support import edge, message, record, seed_chat
    from tests.integration.test_content_storage import upload

    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_team(client, tenant, count=1)
        payload = b"hosted local evidence\x00\xff"
        blob = await upload(database, tenant, payload)
        records = [
            record("chatMessage", f"reply#{i:03}", message(f"reply#{i:03}")) for i in range(201)
        ]
        records.append(
            {
                **record(
                    "chatMessageHostedContent",
                    "hosted#one",
                    {"contentType": "application/octet-stream"},
                ),
                "content_id": str(blob.id),
            }
        )
        edges = [
            edge("chatMessage", "message#0", "replies", "chatMessage", f"reply#{i:03}")
            for i in range(201)
        ]
        edges.append(
            edge(
                "chatMessage",
                "reply#000",
                "hostedContents",
                "chatMessageHostedContent",
                "hosted#one",
            )
        )
        result = await client.post(
            f"/_mock/tenants/{tenant}/workloads/teams/import",
            json={"scope": fixture["team"], "records": records, "edges": edges},
        )
        assert result.status_code == 200, result.text
        auth = await headers_for(database, settings, tenant, app, None, {"ChannelMessage.Read.All"})
        response = await client.get(fixture["base"] + "/messages?$expand=replies", headers=auth)
        assert response.status_code == 200, response.text
        root = response.json()["value"][0]
        assert len(root["replies"]) == 200
        result = await client.get(root["replies@odata.nextLink"], headers=auth)
        assert result.status_code == 200, result.text
        assert len(result.json()["value"]) == 1
        base = fixture["base"] + "/messages/message%230/replies/reply%23000/hostedContents"
        assert (await client.get(base, headers=auth)).json()["value"][0]["id"] == "hosted#one"
        response = await client.get(base + "/hosted%23one/$value", headers=auth)
        assert response.status_code == 200, response.text
        assert response.content == payload
        chat = await seed_chat(client, tenant, count=1)
        await access(
            client,
            tenant,
            chat["chat"],
            "chat",
            chat["chat"],
            app,
            "ChatMessage.Read.Chat",
            "application",
        )
        rsc = await headers_for(database, settings, tenant, app, None, set())
        assert (await client.get(chat["base"] + "/messages", headers=rsc)).status_code == 200
        assert (
            await client.get(chat["base"] + "/messages/message%230/hostedContents", headers=rsc)
        ).status_code == 403
        exported = await client.get(
            f"/_mock/tenants/{tenant}/workloads/teams/export?scope={fixture['team']}"
        )
        assert exported.status_code == 200, exported.text
        assert exported.json()["manifest"]["content"][0]["sha256"] == blob.sha256
        reset = await client.post(
            f"/_mock/tenants/{tenant}/workloads/teams/reset",
            json={"scope": fixture["team"], "expected_revision": 2},
        )
        assert reset.status_code == 200, reset.text
        restored = await client.post(
            f"/_mock/tenants/{tenant}/workloads/teams/import",
            json={**exported.json(), "expected_revision": 0},
        )
        assert restored.status_code == 200, restored.text
        assert (await client.get(base + "/hosted%23one/$value", headers=auth)).content == payload


async def test_files_folder_resolves_canonical_drive_with_separate_permissions(database, settings):
    from tests.integration.site_drive_support import seed_site
    from tests.integration.teams_support import edge, record

    async with graph_client(database, settings) as (client, tenant, app, user):
        fixture = await seed_team(client, tenant)
        site = await seed_site(client, database, tenant, user)
        reference = record(
            "filesFolderReference",
            "folder-reference",
            {"driveId": site["drive"], "itemId": site["root"]},
        )
        result = await client.post(
            f"/_mock/tenants/{tenant}/workloads/teams/import",
            json={
                "scope": fixture["team"],
                "records": [reference],
                "edges": [
                    edge(
                        "channel",
                        fixture["channel"],
                        "filesFolder",
                        "filesFolderReference",
                        "folder-reference",
                    )
                ],
            },
        )
        assert result.status_code == 200, result.text
        messages = await headers_for(
            database, settings, tenant, app, None, {"ChannelMessage.Read.All"}
        )
        assert (
            await client.get(fixture["base"] + "/filesFolder", headers=messages)
        ).status_code == 403
        files = await headers_for(database, settings, tenant, app, None, {"Files.Read.All"})
        response = await client.get(fixture["base"] + "/filesFolder", headers=files)
        assert response.status_code == 200, response.text
        assert response.json()["id"] == site["root"]
        await access(
            client,
            tenant,
            fixture["team"],
            "team",
            fixture["team"],
            app,
            "File.Read.Group",
            "application",
        )
        rsc = await headers_for(database, settings, tenant, app, None, set())
        assert (await client.get(fixture["base"] + "/filesFolder", headers=rsc)).status_code == 200

from mockazure.directory.history import reserve_revision
from mockazure.directory.mutations import add_relation
from tests.integration.graph_support import graph_client, headers_for, persisted_group
from tests.integration.test_graph_mutations import WRITE
from tests.integration.test_graph_query import add_users


async def test_relationship_snapshot_pages_and_nested_expansion(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        rows = await add_users(database, tenant, [f"member{i}" for i in range(105)])
        async with database.sessions.begin() as session:
            group = await persisted_group(session, tenant, "Large")
            small = await persisted_group(session, tenant, "Small")
            revision = await reserve_revision(session, tenant)
            for row in rows:
                await add_relation(session, tenant, group.id, row["id"], "member", revision)
            await add_relation(session, tenant, small.id, user, "member", revision)
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        path = f"/v1.0/groups/{group.id}/members"
        first = await client.get(path + "?$top=50&$select=id,displayName", headers=headers)
        assert first.status_code == 200, first.text
        assert len(first.json()["value"]) == 50
        link = first.json()["@odata.nextLink"]
        expected = sorted((str(row["id"]), row["display_name"]) for row in rows)
        removed = expected[-1][0]
        assert (
            await client.delete(f"/v1.0/groups/{group.id}/members/{removed}/$ref", headers=headers)
        ).status_code == 204
        assert (
            await client.patch(
                f"/v1.0/users/{removed}",
                headers=headers,
                json={"displayName": "Changed after snapshot"},
            )
        ).status_code == 204
        values = first.json()["value"]
        while link:
            page = await client.get(link, headers=headers)
            assert page.status_code == 200, page.text
            values += page.json()["value"]
            link = page.json().get("@odata.nextLink")
        assert [(x["id"], x["displayName"]) for x in values] == expected
        # Collection expansion uses the same original object AND relation snapshot on every page.
        initial = await client.get(
            "/v1.0/groups?$top=1&$orderby=displayName&$expand=members($select=id)", headers=headers
        )
        # Directory expansion is bounded with no nested continuation, as in Graph.
        assert initial.status_code == 200, initial.text
        assert len(initial.json()["value"][0]["members"]) == 20
        assert "members@odata.nextLink" not in initial.json()["value"][0]
        async with database.sessions.begin() as session:
            peer = await persisted_group(session, tenant, "Tiny")
            await add_relation(
                session, tenant, peer.id, user, "member", await reserve_revision(session, tenant)
            )
        initial = await client.get(
            "/v1.0/groups?$filter=displayName ne 'Large'&$top=1&$expand=members",
            headers={**headers, "ConsistencyLevel": "eventual"},
        )
        # Advanced filters plus expand are deliberately unsupported; use ID ordering instead.
        assert initial.status_code == 400
        # Entity expansion always reflects current state.
        expanded = await client.get(
            f"/v1.0/groups/{small.id}?$expand=members($select=id)", headers=headers
        )
        assert [x["id"] for x in expanded.json()["members"]] == [str(user)]


async def test_expanded_collection_page_keeps_original_membership(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        async with database.sessions.begin() as session:
            groups = [await persisted_group(session, tenant, name) for name in ("A", "B")]
            revision = await reserve_revision(session, tenant)
            for group in groups:
                await add_relation(session, tenant, group.id, user, "member", revision)
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        first = await client.get(
            "/v1.0/groups?$top=1&$orderby=displayName&$expand=members($select=id)", headers=headers
        )
        assert first.status_code == 200, first.text
        assert (
            await client.delete(f"/v1.0/groups/{groups[1].id}/members/{user}/$ref", headers=headers)
        ).status_code == 204
        second = await client.get(first.json()["@odata.nextLink"], headers=headers)
        assert second.status_code == 200, second.text
        assert second.json()["value"][0]["members"] == [
            {"id": str(user), "@odata.type": "#microsoft.graph.user"}
        ]

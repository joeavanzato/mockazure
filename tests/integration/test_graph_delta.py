import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from sqlalchemy import insert, update

from mockazure.directory.history import record_change, reserve_revision
from mockazure.directory.models import Continuation, DirectoryRelation, Group, RelationVersion
from mockazure.directory.repository import public_object
from mockazure.directory.types import DirectoryChange
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_graph_mutations import WRITE, group_body, user_body
from tests.integration.test_graph_query import add_users


async def finish(client, headers, response):
    assert response.status_code == 200, response.text
    values = response.json()["value"]
    pages = 1
    while "@odata.nextLink" in response.json():
        assert "@odata.deltaLink" not in response.json()
        response = await client.get(response.json()["@odata.nextLink"], headers=headers)
        assert response.status_code == 200, response.text
        values += response.json()["value"]
        pages += 1
        assert pages < 100
    return values, response.json()["@odata.deltaLink"]


async def test_delta_rounds_and_selection(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        rows = await add_users(database, tenant, [f"before{i}" for i in range(102)])
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        first = await client.get("/v1.0/users/delta?$select=id,displayName", headers=headers)
        assert first.status_code == 200, first.text
        assert "@odata.nextLink" in first.json()
        target = max(rows, key=lambda x: str(x["id"]))
        changed = await client.patch(
            f"/v1.0/users/{target['id']}", headers=headers, json={"displayName": "after"}
        )
        assert changed.status_code == 204
        all_rows, link = await finish(client, headers, first)
        expected = {str(r["id"]): r["display_name"] for r in rows} | {str(user): "Directory Reader"}
        assert {r["id"]: r["displayName"] for r in all_rows} == expected
        second, link = await finish(client, headers, await client.get(link, headers=headers))
        assert second == [{"id": str(target["id"]), "displayName": "after"}]
        assert (
            await client.patch(
                f"/v1.0/users/{target['id']}", headers=headers, json={"department": "unselected"}
            )
        ).status_code == 204
        empty, link = await finish(client, headers, await client.get(link, headers=headers))
        assert empty == []
        created = await client.post("/v1.0/users", headers=headers, json=user_body("added"))
        assert created.status_code == 201, created.text
        assert (
            await client.delete(f"/v1.0/users/{target['id']}", headers=headers)
        ).status_code == 204
        changes, link = await finish(client, headers, await client.get(link, headers=headers))
        assert {r["id"] for r in changes} == {created.json()["id"], str(target["id"])}
        deleted = next(r for r in changes if r["id"] == str(target["id"]))
        assert deleted == {"id": str(target["id"]), "@removed": {"reason": "deleted"}}
        assert (await client.get(link, headers=headers)).json()["value"] == []


async def test_group_membership_delta_chunks_and_selection(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        group = (await client.post("/v1.0/groups", headers=headers, json=group_body())).json()["id"]
        _, all_link = await finish(
            client, headers, await client.get("/v1.0/groups/delta", headers=headers)
        )
        _, selected_link = await finish(
            client,
            headers,
            await client.get("/v1.0/groups/delta?$select=id,displayName", headers=headers),
        )
        rows = await add_users(database, tenant, [f"member{i}" for i in range(1001)])
        # Bulk inert fixture mutation, one committed revision with matching public history.
        async with database.sessions.begin() as session:
            revision = await reserve_revision(session, tenant)
            edges = [
                dict(tenant_id=tenant, group_id=UUID(group), object_id=r["id"], relation="member")
                for r in rows
            ]
            await session.execute(insert(DirectoryRelation), edges)
            await session.execute(
                insert(RelationVersion), [dict(e, valid_from=revision) for e in edges]
            )
            obj = public_object(await session.get(Group, UUID(group)))
            await record_change(
                session,
                DirectoryChange(revision, "group", obj.id, frozenset({"members"})),
                obj,
                obj,
                {"operation": "fixture", "provenance": "test"},
            )
        first = await client.get(all_link, headers=headers)
        assert first.status_code == 200, first.text
        assert "@odata.nextLink" in first.json()
        assert len(first.json()["value"][0]["members@delta"]) == 1000
        values, link = await finish(client, headers, first)
        assert len(values) == 2 and {r["id"] for r in values} == {group}
        assert {m["id"] for r in values for m in r["members@delta"]} == {str(r["id"]) for r in rows}
        assert all(
            m["@odata.type"] == "#microsoft.graph.user" for r in values for m in r["members@delta"]
        )
        excluded, _ = await finish(
            client, headers, await client.get(selected_link, headers=headers)
        )
        assert excluded == []
        removed_id = str(rows[0]["id"])
        assert (
            await client.delete(f"/v1.0/groups/{group}/members/{removed_id}/$ref", headers=headers)
        ).status_code == 204
        values, _ = await finish(client, headers, await client.get(link, headers=headers))
        assert values[0]["members@delta"] == [
            {
                "id": removed_id,
                "@odata.type": "#microsoft.graph.user",
                "@removed": {"reason": "deleted"},
            }
        ]


async def test_delta_contract_expiry_and_binding(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        aliases = ["/v1.0/users/delta", "/v1.0/users/microsoft.graph.delta", "/beta/users/delta"]
        for alias in aliases:
            assert (await client.get(alias, headers=headers)).status_code == 200
        for option in (
            "$top=1",
            "$orderby=id",
            "$filter=displayName eq 'x'",
            "$expand=members",
            "$select=id&$select=displayName",
            "$search=x",
            "$select=passwordProfile",
        ):
            assert (
                await client.get("/v1.0/users/delta?" + option, headers=headers)
            ).status_code == 400
        _, link = await finish(
            client,
            headers,
            await client.get("/v1.0/users/delta?$select=id,displayName", headers=headers),
        )
        assert (
            await client.get(link.replace("/v1.0/", "/beta/"), headers=headers)
        ).status_code == 400
        assert (await client.get(link + "&$select=id", headers=headers)).status_code == 400
        assert (
            await client.get("/v1.0/users/delta?$deltatoken=forged", headers=headers)
        ).status_code == 400
        token = parse_qs(urlsplit(link).query)["$deltatoken"][0]
        async with database.sessions.begin() as session:
            await session.execute(
                update(Continuation)
                .where(Continuation.token_hash == hashlib.sha256(token.encode()).hexdigest())
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        expired = await client.get(link, headers=headers)
        assert expired.status_code == 410, expired.text
        assert expired.json()["error"]["code"] == "syncStateNotFound"
        location = expired.headers["Location"]
        assert location.startswith("https://localhost:8443/v1.0/users/delta?")
        assert parse_qs(urlsplit(location).query, keep_blank_values=True) == {
            "$deltatoken": [""],
            "$select": ["id,displayName"],
        }
        restored, _ = await finish(client, headers, await client.get(location, headers=headers))
        assert len(restored) == 1


async def test_expired_delta_after_cleanup_still_requests_resynchronization(database, settings):
    from mockazure.directory.history import prune_history

    async with graph_client(database, settings) as (client, tenant, app, _):
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        _, link = await finish(
            client, headers, await client.get("/v1.0/users/delta", headers=headers)
        )
        async with database.sessions.begin() as session:
            await session.execute(
                update(Continuation)
                .where(Continuation.tenant_id == tenant)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await prune_history(session, tenant, datetime.now(UTC))
        expired = await client.get(link, headers=headers)
        assert expired.status_code == 410, expired.text
        assert expired.json()["error"]["code"] == "syncStateNotFound"
        assert parse_qs(urlsplit(expired.headers["Location"]).query, keep_blank_values=True) == {
            "$deltatoken": [""]
        }


async def test_delta_hidden_membership_and_implicit_removal(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        group = (await client.post("/v1.0/groups", headers=headers, json=group_body())).json()["id"]
        assert (
            await client.post(
                f"/v1.0/groups/{group}/members/$ref",
                headers=headers,
                json={"@odata.id": f"https://localhost:8443/v1.0/users/{user}"},
            )
        ).status_code == 204
        initial, link = await finish(
            client, headers, await client.get("/v1.0/groups/delta", headers=headers)
        )
        assert initial[0]["members@delta"][0]["id"] == str(user)
        assert (await client.delete(f"/v1.0/users/{user}", headers=headers)).status_code == 204
        # Microsoft's group delta contract does not report implicit removal on member deletion.
        changes, _ = await finish(client, headers, await client.get(link, headers=headers))
        assert changes == []
        async with database.sessions.begin() as session:
            row = await session.get(Group, UUID(group))
            row.profile = {**row.profile, "visibility": "HiddenMembership"}
        assert (await client.get("/v1.0/groups/delta", headers=headers)).status_code == 403
        projected = await client.get("/v1.0/groups/delta?$select=id,displayName", headers=headers)
        assert projected.status_code == 200

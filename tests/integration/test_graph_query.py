from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import insert, text

from mockazure.directory.models import DirectoryEntry, DirectoryRelation, ObjectVersion
from mockazure.identity.models import User
from tests.integration.graph_support import (
    graph_client,
    headers_for,
    persisted_group,
    seed_relation_versions,
)


async def add_users(database, tenant, names):
    # Inert test identities; exercise SQL-backed provider behavior without password work.
    rows = [
        {
            "id": uuid4(),
            "tenant_id": tenant,
            "upn": f"query{i}@contoso.test",
            "display_name": name,
            "verifier": "!",
            "enabled": i % 2 == 0,
            "profile": {},
            "created_at": datetime(2026, 10, 1, tzinfo=UTC),
        }
        for i, name in enumerate(names)
    ]
    async with database.sessions.begin() as session:
        from mockazure.directory.history import reserve_revision

        revision = await reserve_revision(session, tenant)
        await session.execute(insert(User), rows)
        await session.execute(
            insert(DirectoryEntry),
            [
                {"tenant_id": tenant, "id": row["id"], "kind": "user", "user_id": row["id"]}
                for row in rows
            ],
        )
        await session.execute(
            insert(ObjectVersion),
            [
                {
                    "tenant_id": tenant,
                    "object_id": row["id"],
                    "kind": "user",
                    "valid_from": revision,
                    "display_name": row["display_name"],
                    "upn": row["upn"],
                    "created_at": row["created_at"],
                    "account_enabled": row["enabled"],
                    "properties": {
                        "id": str(row["id"]),
                        "displayName": row["display_name"],
                        "userPrincipalName": row["upn"],
                        "accountEnabled": row["enabled"],
                        "mail": None,
                        "createdDateTime": "2026-10-01T00:00:00Z",
                    },
                    "changed_fields": ["id"],
                }
                for row in rows
            ],
        )
    return rows


async def test_query_results_projection_count_and_escaped_literals(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        rows = await add_users(database, tenant, ["O'Brien", "李", "ordinary", "x' or true"])
        await seed_relation_versions(database, tenant)
        headers = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        response = await client.get(
            "/v1.0/users",
            headers=headers,
            params={
                "$filter": "displayName eq 'O''Brien'",
                "$select": "id,displayName",
                "$count": "true",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["value"] == [{"id": str(rows[0]["id"]), "displayName": "O'Brien"}]
        assert "@odata.count" not in response.json()
        for expression, expected in [
            ("displayName eq '李'", [str(rows[1]["id"])]),
            ("accountEnabled eq false", sorted([str(rows[1]["id"]), str(rows[3]["id"])])),
            (
                "createdDateTime le 2026-10-01T00:00:00Z and mail eq null",
                sorted(str(row["id"]) for row in rows),
            ),
            ("displayName eq 'x'' or true'", [str(rows[3]["id"])]),
        ]:
            result = await client.get(
                "/beta/users", headers=headers, params={"$filter": expression, "$select": "id"}
            )
            assert result.status_code == 200, result.text
            assert sorted(row["id"] for row in result.json()["value"]) == expected
        query = {
            "$filter": "startsWith(userPrincipalName,'query')",
            "$orderby": "displayName",
            "$count": "true",
            "$select": "id,displayName",
        }
        assert (await client.get("/v1.0/users", headers=headers, params=query)).status_code == 400
        advanced = {**headers, "ConsistencyLevel": "eventual"}
        result = await client.get("/v1.0/users", headers=advanced, params=query)
        assert result.status_code == 200, result.text
        assert result.json()["@odata.count"] == 4
        assert (await client.get("/v1.0/users/$count", headers=headers)).status_code == 400
        count = await client.get("/v1.0/users/$count", headers=advanced)
        assert count.status_code == 200
        assert count.text == "5"


async def test_transitive_cycles_and_expansion_boundaries(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user_id):
        rows = await add_users(database, tenant, [f"member{i}" for i in range(21)])
        async with database.sessions.begin() as session:
            first = await persisted_group(session, tenant, "First")
            second = await persisted_group(session, tenant, "Second")
            ids = first.id, second.id
            for source, target in [
                (first.id, second.id),
                (second.id, first.id),
                (second.id, user_id),
            ]:
                session.add(
                    DirectoryRelation(
                        tenant_id=tenant, group_id=source, object_id=target, relation="member"
                    )
                )
            for row in rows:
                session.add(
                    DirectoryRelation(
                        tenant_id=tenant, group_id=first.id, object_id=row["id"], relation="member"
                    )
                )
        await seed_relation_versions(database, tenant)
        headers = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        response = await client.get(f"/v1.0/groups/{ids[0]}/transitiveMembers", headers=headers)
        assert response.status_code == 200, response.text
        members = [row["id"] for row in response.json()["value"]]
        assert len(members) == len(set(members))
        assert str(user_id) in members and str(ids[1]) in members
        assert str(ids[0]) not in members
        inverse = await client.get(f"/v1.0/users/{user_id}/transitiveMemberOf", headers=headers)
        assert {row["id"] for row in inverse.json()["value"]} == {str(x) for x in ids}
        expansion = await client.get(f"/v1.0/groups/{ids[0]}?$expand=members", headers=headers)
        assert expansion.status_code == 200, expansion.text
        assert len(expansion.json()["members"]) == 20
        assert "members@odata.nextLink" not in expansion.json()
        direct = await client.get(f"/v1.0/groups/{ids[0]}/members", headers=headers)
        assert len(direct.json()["value"]) == 22


async def test_selected_query_is_bounded_at_5000_users(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        rows = await add_users(database, tenant, [f"Person{i:05}" for i in range(5000)])
        await seed_relation_versions(database, tenant)
        headers = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        response = await client.get(
            "/v1.0/users",
            headers=headers,
            params={
                "$filter": "displayName eq 'Person04999'",
                "$select": "id,displayName",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["value"] == [
            {"id": str(rows[-1]["id"]), "displayName": "Person04999"}
        ]
        async with database.sessions() as session:
            await session.execute(text("ANALYZE directory_versions"))
            plan = (
                await session.execute(
                    text(
                        "EXPLAIN (FORMAT JSON) SELECT object_id FROM directory_versions "
                        "WHERE tenant_id=:tenant AND kind='user' AND display_name='Person04999' "
                        "AND valid_to IS NULL LIMIT 101"
                    ),
                    {"tenant": tenant},
                )
            ).scalar()
            assert plan[0]["Plan"]["Node Type"] == "Limit"
            assert plan[0]["Plan"]["Plans"][0]["Node Type"] in {"Index Scan", "Bitmap Heap Scan"}


async def test_queries_cannot_infer_hidden_fields_or_expand_hidden_members(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user_id):
        await seed_relation_versions(database, tenant)
        basic = await headers_for(
            database, settings, tenant, app, user_id, {"User.ReadBasic.All"}, "delegated"
        )
        response = await client.get(
            "/v1.0/users", headers=basic, params={"$filter": "accountEnabled eq true"}
        )
        assert response.status_code == 403
        async with database.sessions.begin() as session:
            parent = await persisted_group(session, tenant, "Visible")
            hidden = await persisted_group(session, tenant, "Hidden")
            hidden.profile = {"visibility": "HiddenMembership"}
            parent_id, hidden_id = parent.id, hidden.id
            session.add(
                DirectoryRelation(
                    tenant_id=tenant, group_id=parent_id, object_id=hidden_id, relation="member"
                )
            )
            session.add(
                DirectoryRelation(
                    tenant_id=tenant, group_id=hidden_id, object_id=user_id, relation="member"
                )
            )
        await seed_relation_versions(database, tenant)
        full = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        nested = await client.get(f"/v1.0/groups/{parent_id}/transitiveMembers", headers=full)
        assert nested.status_code == 403
        expanded = await client.get(f"/v1.0/groups/{hidden_id}?$expand=members", headers=full)
        assert expanded.status_code == 403
        await seed_relation_versions(database, tenant)
        allowed = await headers_for(
            database, settings, tenant, app, None, {"Directory.Read.All", "Member.Read.Hidden"}
        )
        assert (
            await client.get(f"/v1.0/groups/{parent_id}/transitiveMembers", headers=allowed)
        ).status_code == 200


async def test_array_any_and_startswith_execute_as_bound_sql(database, settings):
    from mockazure.directory.history import record_change, reserve_revision
    from mockazure.directory.models import Group
    from mockazure.directory.repository import public_object
    from mockazure.directory.types import DirectoryChange

    async with graph_client(database, settings) as (client, tenant, app, _):
        async with database.sessions.begin() as session:
            row = Group(
                tenant_id=tenant,
                display_name="A_%literal",
                mail_nickname="literal",
                profile={"groupTypes": ["Unified"], "mailEnabled": True, "securityEnabled": False},
            )
            session.add(row)
            await session.flush()
            obj = public_object(row)
            revision = await reserve_revision(session, tenant)
            await record_change(
                session,
                DirectoryChange(revision, "group", row.id, frozenset(obj.properties)),
                None,
                obj,
                {"operation": "create", "provenance": "test-fixture"},
            )
            wanted = str(row.id)
            await persisted_group(session, tenant, "A12literal")
        await seed_relation_versions(database, tenant)
        headers = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        for expression in (
            "groupTypes/any(t: t eq 'Unified' or t eq 'DynamicMembership')",
            "startswith(displayName,'A_%')",
        ):
            response = await client.get(
                "/v1.0/groups", headers=headers, params={"$filter": expression, "$select": "id"}
            )
            assert response.status_code == 200, response.text
            assert response.json()["value"] == [{"id": wanted}]

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from mockazure.admin.app import create_app
from mockazure.db.engine import close_database, create_database
from mockazure.directory.history import record_change, reserve_revision
from mockazure.directory.models import DirectoryState, ObjectVersion
from mockazure.directory.repository import public_object
from mockazure.directory.types import DirectoryChange
from mockazure.identity.models import User
from mockazure.providers.graph.errors import GraphError
from tests.integration.graph_support import graph_client, headers_for, registered_app
from tests.integration.test_graph_query import add_users


async def rename_or_delete(database, tenant, user_id, *, deleted=False):
    async with database.sessions.begin() as session:
        revision = await reserve_revision(session, tenant)
        row = await session.get(User, user_id)
        before = public_object(row)
        if deleted:
            row.deleted_at, row.enabled = datetime.now(UTC), False
        else:
            row.display_name = "ZZZ Renamed after snapshot"
        await session.flush()
        await record_change(
            session,
            DirectoryChange(revision, "user", row.id, frozenset({"displayName"}), deleted),
            before,
            None if deleted else public_object(row),
            {"operation": "delete" if deleted else "update", "provenance": "test-fixture"},
        )


async def test_paging_snapshot_under_mutation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, first_user):
        rows = await add_users(database, tenant, [f"Person{i:05}" for i in range(4999)])
        headers = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        first = await client.get(
            "/v1.0/users",
            headers=headers,
            params={
                "$orderby": "displayName",
                "$top": "137",
                "$select": "id,displayName",
            },
        )
        assert first.status_code == 200, first.text
        expected = [{"id": str(first_user), "displayName": "Directory Reader"}] + [
            {"id": str(row["id"]), "displayName": row["display_name"]} for row in rows
        ]
        seen = first.json()["value"]
        await rename_or_delete(database, tenant, rows[3500]["id"])
        await rename_or_delete(database, tenant, rows[4200]["id"], deleted=True)
        added = await client.post(
            f"/_mock/tenants/{tenant}/identity/users",
            json={
                "upn": "new@contoso.test",
                "display_name": "AAA New after snapshot",
                "password": "local-new-password",
            },
        )
        assert added.status_code == 201
        link = first.json().get("@odata.nextLink")
        assert link
        while link:
            response = await client.get(link, headers=headers)
            assert response.status_code == 200, response.text
            seen.extend(response.json()["value"])
            link = response.json().get("@odata.nextLink")
        assert seen == expected
        assert len({item["id"] for item in seen}) == 5000
        fresh = await client.get(
            "/v1.0/users",
            headers=headers,
            params={"$filter": "displayName eq 'AAA New after snapshot'", "$select": "id"},
        )
        assert fresh.json()["value"] == [{"id": added.json()["id"]}]


async def test_cursor_restart_replay_and_context_binding(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user_id):
        await add_users(database, tenant, ["A", "B", "C"])
        headers = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        first = await client.get("/v1.0/users?$top=1", headers=headers)
        assert first.status_code == 200, first.text
        link = first.json()["@odata.nextLink"]
        page_a = await client.get(link, headers=headers)
        replica = create_database(settings)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(settings, replica)),
                base_url="https://localhost:8443",
            ) as other:
                page_b = await other.get(link, headers=headers)
                assert page_b.json() == page_a.json()
                await replica.engine.dispose()
                after_restart = await other.get(link, headers=headers)
                assert after_restart.json() == page_a.json()
        finally:
            await close_database(replica)
        for bad in (
            link + "&$select=id",
            link.replace("/v1.0/", "/beta/"),
            link.replace("/users?", "/groups?"),
        ):
            assert (await client.get(bad, headers=headers)).status_code == 400
        token = parse_qs(urlsplit(link).query)["$skiptoken"][0]
        tampered = link.replace(token, ("A" if token[0] != "A" else "B") + token[1:])
        assert (await client.get(tampered, headers=headers)).status_code == 400
        different_client = await headers_for(
            database,
            settings,
            tenant,
            await registered_app(client, tenant),
            None,
            {"Directory.Read.All"},
        )
        assert (await client.get(link, headers=different_client)).status_code == 400
        changed_permissions = await headers_for(
            database, settings, tenant, app, None, {"User.Read.All"}
        )
        assert (await client.get(link, headers=changed_permissions)).status_code == 400
        delegated = await headers_for(
            database, settings, tenant, app, user_id, {"Directory.Read.All"}, "delegated"
        )
        assert (await client.get(link, headers=delegated)).status_code == 400
        foreign_tenant = (
            await client.post(
                "/_mock/tenants",
                json={
                    "slug": "other",
                    "display_name": "Other",
                    "domain": "other.test",
                },
            )
        ).json()["id"]
        foreign = await headers_for(
            database,
            settings,
            foreign_tenant,
            await registered_app(client, foreign_tenant),
            None,
            {"Directory.Read.All"},
        )
        assert (await client.get(link, headers=foreign)).status_code == 400


async def test_history_cleanup_preserves_boundary_and_expires_handles(database, settings):
    from mockazure.directory.history import prune_history
    from mockazure.providers.graph.continuations import (
        CursorBinding,
        CursorState,
        issue_cursor,
        resolve_cursor,
    )
    from mockazure.providers.graph.query.contracts import contract_for
    from mockazure.providers.graph.query.parser import parse_query

    async with graph_client(database, settings) as (_, tenant, app, user_id):
        now = datetime.now(UTC)
        query = parse_query([], contract_for("v1.0", "users"), None)
        async with database.sessions.begin() as session:
            directory = await session.get(DirectoryState, tenant)
            binding = CursorBinding(
                tenant,
                directory.generation,
                "v1.0",
                "users",
                app,
                None,
                "application",
                "test-permission-fingerprint",
            )
            state = CursorState(
                binding, query, None, directory.revision, None, "page", now + timedelta(hours=1)
            )
            token = await issue_cursor(session, state)
        await rename_or_delete(database, tenant, user_id)
        async with database.sessions.begin() as session:
            await prune_history(session, tenant, now)
            restored = await resolve_cursor(session, token, binding, now)
            assert restored.upper_revision == state.upper_revision
            versions = (
                await session.scalars(
                    select(ObjectVersion)
                    .where(ObjectVersion.tenant_id == tenant)
                    .order_by(ObjectVersion.valid_from)
                )
            ).all()
            assert len(versions) == 2
            assert versions[0].properties["displayName"] == "Directory Reader"
        async with database.sessions.begin() as session:
            with pytest.raises(GraphError) as expired:
                await resolve_cursor(session, token, binding, now + timedelta(hours=2))
            assert expired.value.status == 400
            with pytest.raises(GraphError):
                await resolve_cursor(session, token, replace(binding, generation=uuid4()), now)
            await prune_history(session, tenant, now + timedelta(hours=2))
            assert (
                len(
                    (
                        await session.scalars(
                            select(ObjectVersion).where(ObjectVersion.tenant_id == tenant)
                        )
                    ).all()
                )
                == 1
            )


async def test_cleanup_cannot_race_first_cursor_capture(database, settings):
    from mockazure.directory.history import lock_history, prune_history

    async with graph_client(database, settings) as (_, tenant, _, _):
        started = asyncio.Event()

        async def cleanup():
            async with database.sessions.begin() as session:
                started.set()
                await prune_history(session, tenant, datetime.now(UTC))

        async with database.sessions() as reader:
            await lock_history(reader, tenant)
            task = asyncio.create_task(cleanup())
            try:
                await started.wait()
                done, _ = await asyncio.wait({task}, timeout=0.15)
                assert not done
                await reader.commit()
                await asyncio.wait_for(task, 5)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)


async def test_generation_invalidation_is_tenant_scoped(database, settings):
    from mockazure.db.models import Tenant
    from mockazure.providers.graph.continuations import (
        CursorBinding,
        CursorState,
        issue_cursor,
        resolve_cursor,
    )
    from mockazure.providers.graph.query.contracts import contract_for
    from mockazure.providers.graph.query.parser import parse_query

    async with graph_client(database, settings) as (_, tenant, app, _):
        now = datetime.now(UTC)
        query = parse_query([], contract_for("v1.0", "users"), None)
        async with database.sessions.begin() as session:
            other = Tenant(
                slug="paging-independent",
                display_name="Independent",
                domain="paging-independent.test",
                licences=[],
            )
            session.add(other)
            await session.flush()
            generation = uuid4()
            session.add(DirectoryState(tenant_id=other.id, revision=0, generation=generation))
            first = await session.get(DirectoryState, tenant)
            binding = CursorBinding(
                tenant,
                first.generation,
                "v1.0",
                "users",
                app,
                None,
                "application",
                "same-permissions",
            )
            independent = replace(binding, tenant_id=other.id, generation=generation)
            state = CursorState(
                binding, query, None, first.revision, None, "page", now + timedelta(hours=1)
            )
            token = await issue_cursor(session, state)
            other_token = await issue_cursor(
                session, replace(state, binding=independent, upper_revision=0)
            )
        async with database.sessions.begin() as session:
            (await session.get(DirectoryState, tenant)).generation = uuid4()
        async with database.sessions() as session:
            with pytest.raises(GraphError):
                await resolve_cursor(session, token, binding, now)
            restored = await resolve_cursor(session, other_token, independent, now)
            assert restored.binding.tenant_id == independent.tenant_id


async def test_delegated_page_rechecks_subject_and_enabled_state(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user_id):
        await add_users(database, tenant, ["A", "B"])
        headers = await headers_for(
            database, settings, tenant, app, user_id, {"User.Read.All"}, "delegated"
        )
        first = await client.get("/v1.0/users?$top=1", headers=headers)
        assert first.status_code == 200, first.text
        link = first.json()["@odata.nextLink"]
        another = await client.post(
            f"/_mock/tenants/{tenant}/identity/users",
            json={
                "upn": "second@contoso.test",
                "display_name": "Second",
                "password": "local-second-password",
            },
        )
        assert another.status_code == 201
        other_headers = await headers_for(
            database, settings, tenant, app, another.json()["id"], {"User.Read.All"}, "delegated"
        )
        assert (await client.get(link, headers=other_headers)).status_code == 400
        disabled = await client.patch(
            f"/_mock/tenants/{tenant}/identity/users/{user_id}", json={"enabled": False}
        )
        assert disabled.status_code == 200
        assert (await client.get(link, headers=headers)).status_code == 403

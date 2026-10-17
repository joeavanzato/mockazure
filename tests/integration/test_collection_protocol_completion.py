"""Collection protocol regressions against local PostgreSQL and actual ASGI routes."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from sqlalchemy import select

from mockazure.admin.app import create_app
from mockazure.directory.models import DirectoryRelation
from tests.integration.graph_support import (
    graph_client,
    headers_for,
    persisted_group,
    seed_relation_versions,
)


async def test_batch_extended_roots_keep_item_authorization_and_errors(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        from tests.integration.test_graph_licensing import seed_sku

        await seed_sku(client, tenant)
        headers = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {
                "Directory.Read.All",
                "AuditLog.Read.All",
                "SecurityAlert.Read.All",
                "RoleManagement.Read.Directory",
            },
        )
        paths = {
            "object": f"/directoryObjects/{user}",
            "applications": "/applications?$select=id,displayName",
            "devices": "/devices",
            "report": "/reports/authenticationMethods/userRegistrationDetails",
            "alerts": "/security/alerts_v2?$count=true",
            "roles": "/roleManagement/directory/roleDefinitions?$select=id",
            "denied": "/identity/conditionalAccess/policies",
            "invalid": "/applications?$select=secretText",
            "missing": f"/devices/{uuid4()}",
        }
        response = await client.post(
            "/v1.0/$batch",
            headers=headers,
            json={
                "requests": [
                    {"id": key, "method": "GET", "url": value} for key, value in paths.items()
                ]
            },
        )
        assert response.status_code == 200, response.text
        items = {x["id"]: x for x in response.json()["responses"]}
        assert {key: item["status"] for key, item in items.items()} == {
            key: (
                403
                if key == "denied"
                else 400
                if key == "invalid"
                else 404
                if key == "missing"
                else 200
            )
            for key in paths
        }
        assert items["object"]["body"]["id"] == str(user)
        assert items["alerts"]["body"]["@odata.count"] == 0
        assert items["alerts"]["body"]["value"] == []


async def test_shared_collection_budget_and_single_batch_charge(database, settings):
    settings = settings.model_copy(
        update={"graph_collection_request_limit": 2, "graph_collection_window_seconds": 60}
    )
    now = datetime.now(UTC)
    async with graph_client(database, settings) as (first, tenant, app, user):
        second_app = create_app(settings, database)
        first._transport.app.state.clock = lambda: now
        second_app.state.clock = lambda: now
        headers = await headers_for(database, settings, tenant, app, None, {"Application.Read.All"})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second_app), base_url="https://localhost:8443"
        ) as second:
            a = await first.get("/v1.0/applications", headers=headers)
            b = await second.get("/v1.0/applications", headers=headers)
            assert a.status_code == b.status_code == 200
            expected = {x["id"] for x in a.json()["value"]}
            denied = await first.get("/v1.0/applications", headers=headers)
            assert denied.status_code == 429, denied.text
            retry = int(denied.headers["Retry-After"])
            assert 1 <= retry <= 60
            now += timedelta(seconds=retry + 1)
            batch = await second.post(
                "/v1.0/$batch",
                headers=headers,
                json={
                    "requests": [
                        {"id": str(i), "method": "GET", "url": "/applications"} for i in range(3)
                    ]
                },
            )
            assert batch.status_code == 200, batch.text
            items = batch.json()["responses"]
            assert [x["status"] for x in items] == [200, 200, 429]
            assert {x["id"] for x in items[0]["body"]["value"]} == expected
            assert int(items[-1]["headers"]["retry-after"]) > 0


async def test_collection_continuation_fault_retries_same_snapshot_across_instances(
    database, settings
):
    async with graph_client(database, settings) as (first, tenant, app, user):
        from mockazure.identity.models import Application
        from tests.integration.graph_support import registered_app

        await registered_app(first, tenant)
        headers = await headers_for(database, settings, tenant, app, None, {"Application.Read.All"})
        first_page = await first.get(
            "/v1.0/applications?$top=1&$select=id,displayName", headers=headers
        )
        assert first_page.status_code == 200, first_page.text
        next_link = first_page.json()["@odata.nextLink"]
        async with database.sessions.begin() as session:
            expected = set(
                str(x)
                for x in await session.scalars(
                    select(Application.object_id).where(Application.tenant_id == tenant)
                )
            )
            rows = await session.scalars(select(Application).where(Application.tenant_id == tenant))
            for row in rows:
                row.display_name = "Changed after snapshot"
        fault = await first.post(
            f"/_mock/tenants/{tenant}/graph/faults",
            json={
                "client_id": str(app),
                "resource": "applications",
                "kind": "next_page_429",
                "retry_after": 2,
            },
        )
        assert fault.status_code == 201, fault.text
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings, database)),
            base_url="https://localhost:8443",
        ) as second:
            # The pending continuation fault does not affect a fresh read.
            assert (await second.get("/v1.0/applications", headers=headers)).status_code == 200
            denied = await second.get(next_link, headers=headers)
            assert denied.status_code == 429, denied.text
            assert denied.headers["Retry-After"] == "2"
            page = await second.get(next_link, headers=headers)
            assert page.status_code == 200, page.text
            assert page.json()["value"][0]["displayName"] != "Changed after snapshot"
            seen = {x["id"] for x in first_page.json()["value"] + page.json()["value"]}
            assert seen == expected
            from urllib.parse import parse_qs, urlencode, urlsplit

            parts = urlsplit(next_link)
            token = parse_qs(parts.query)["$skiptoken"][0]
            changed = token[:-1] + ("A" if token[-1] != "A" else "B")
            tampered = parts.path + "?" + urlencode({"$skiptoken": changed})
            assert (await second.get(tampered, headers=headers)).status_code == 400
            async with database.sessions.begin() as session:
                row = await session.get(Application, app)
                row.enabled = False
            assert (await second.get(next_link, headers=headers)).status_code == 403


async def test_directory_count_header_semantics_and_empty_zero(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        for path in ("/v1.0/users", "/v1.0/applications"):
            params = {"$count": "true", "$filter": "displayName eq 'missing-object'"}
            ignored = await client.get(path, params=params, headers=headers)
            assert ignored.status_code == 200, ignored.text
            assert "@odata.count" not in ignored.json()
            advanced = await client.get(
                path,
                params={"$filter": "displayName ne 'missing-object'", "$count": "true"},
                headers=headers,
            )
            assert advanced.status_code == 400, advanced.text
            response = await client.get(
                path, params=params, headers={**headers, "ConsistencyLevel": "eventual"}
            )
            assert response.status_code == 200, response.text
            assert response.json()["@odata.count"] == 0
            assert response.json()["value"] == []


async def test_group_expansion_is_bounded_without_nested_next_link(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        async with database.sessions.begin() as session:
            parent = await persisted_group(session, tenant, "Parent")
            parent_id = parent.id
            children = [await persisted_group(session, tenant, f"Child{i}") for i in range(23)]
            child_ids = {str(x.id) for x in children}
            session.add_all(
                [
                    DirectoryRelation(
                        tenant_id=tenant, group_id=parent_id, object_id=x.id, relation="member"
                    )
                    for x in children
                ]
            )
        await seed_relation_versions(database, tenant)
        headers = await headers_for(
            database, settings, tenant, app, None, {"Group.Read.All", "GroupMember.ReadWrite.All"}
        )
        response = await client.get(
            f"/v1.0/groups/{parent_id}?$select=id&$expand=members($select=id,displayName)",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["members"]) == 20
        assert "members@odata.nextLink" not in body
        assert all(set(x) == {"id", "displayName", "@odata.type"} for x in body["members"])
        seen = set()
        link = f"/v1.0/groups/{parent_id}/members?$top=10&$select=id,displayName"
        while link:
            page = await client.get(link, headers=headers)
            assert page.status_code == 200, page.text
            seen.update(x["id"] for x in page.json()["value"])
            link = page.json().get("@odata.nextLink")
        assert seen == child_ids


async def test_resource_specific_options_do_not_accept_undocumented_shortcuts(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        from tests.integration.test_graph_licensing import seed_sku

        await seed_sku(client, tenant)
        headers = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {"AuditLog.Read.All", "SecurityAlert.Read.All", "SecurityEvents.Read.All"},
        )
        for path in (
            "/v1.0/auditLogs/signIns",
            "/v1.0/auditLogs/directoryAudits",
            "/v1.0/reports/authenticationMethods/userRegistrationDetails",
            "/v1.0/security/alerts_v2",
            "/v1.0/security/secureScores",
        ):
            response = await client.get(path, params={"$select": "id"}, headers=headers)
            assert response.status_code == 400, response.text


async def test_shared_budget_commits_when_callers_read_transaction_rolls_back(database, settings):
    import pytest

    from mockazure.identity.types import PrincipalContext
    from mockazure.providers.graph.errors import GraphError
    from mockazure.providers.graph.throttle import enforce_read_budget

    async with graph_client(database, settings) as (client, tenant, app, user):
        context = PrincipalContext(
            tenant,
            app,
            None,
            "application",
            "00000003-0000-0000-c000-000000000000",
            frozenset({"Application.Read.All"}),
        )
        now = datetime.now(UTC)
        with pytest.raises(RuntimeError, match="request aborted"):
            async with database.sessions.begin() as session:
                session.info["graph_read_budget"] = {"limit": 1, "window_seconds": 60}
                await enforce_read_budget(session, context, "applications", now)
                raise RuntimeError("request aborted")
        for _ in range(2):
            with pytest.raises(GraphError) as error:
                async with database.sessions.begin() as session:
                    session.info["graph_read_budget"] = {"limit": 1, "window_seconds": 60}
                    await enforce_read_budget(session, context, "applications", now)
            assert error.value.status == 429
            assert int(error.value.headers["Retry-After"]) > 0


async def test_shared_budget_does_not_require_two_pool_connections(database, settings):
    import asyncio

    from mockazure.db.engine import close_database, create_database

    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(database, settings, tenant, app, None, {"Application.Read.All"})
        narrow = create_database(
            settings.model_copy(update={"pool_size": 1, "pool_max_overflow": 0})
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(settings, narrow)),
                base_url="https://localhost:8443",
            ) as second:
                response = await asyncio.wait_for(
                    second.get("/v1.0/applications", headers=headers), timeout=3
                )
                assert response.status_code == 200, response.text
        finally:
            await close_database(narrow)


async def test_read_budget_capabilities_match_configured_limits(database, settings):
    settings = settings.model_copy(
        update={"graph_collection_request_limit": 3, "graph_collection_window_seconds": 17}
    )
    async with graph_client(database, settings) as (client, tenant, app, user):
        response = await client.get("/_mock/capabilities")
        assert response.status_code == 200
        profile = response.json()["graph_read_budgets"]
        assert profile["collection_limit"] == 3
        assert profile["collection_window_seconds"] == 17
        assert {"applications", "userRegistrationDetails", "security.alerts_v2"} <= set(
            profile["resources"]
        )
        assert profile["batch_envelope_charged"] is False


async def test_budget_migration_preserves_existing_counters_and_faults(settings):
    import pytest
    from alembic import command
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError

    from mockazure.db.migrations import configuration, migrate

    url = settings.database_url.get_secret_value()
    engine = create_engine(url, hide_parameters=True)
    tenant, app = uuid4(), uuid4()
    try:
        with engine.begin() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "039")
            connection.execute(
                text(
                    "INSERT INTO tenants (id,slug,display_name,domain,licences,revision) "
                    "VALUES (:id,'budget','Budget','budget.test','[]',1)"
                ),
                {"id": tenant},
            )
            connection.execute(
                text("INSERT INTO graph_request_budgets VALUES (:tenant,:app,'users',now(),7)"),
                {"tenant": tenant, "app": app},
            )
            connection.execute(
                text(
                    "INSERT INTO graph_read_faults VALUES (:tenant,:app,'users','next_page_429',9)"
                ),
                {"tenant": tenant, "app": app},
            )
        migrate(url)
        with engine.begin() as connection:
            assert (
                connection.scalar(
                    text("SELECT used FROM graph_request_budgets WHERE resource='users'")
                )
                == 7
            )
            assert (
                connection.scalar(
                    text("SELECT retry_after FROM graph_read_faults WHERE resource='users'")
                )
                == 9
            )
            connection.execute(
                text(
                    "INSERT INTO graph_request_budgets VALUES "
                    "(:tenant,:app,'security.secureScoreControlProfiles',now(),1)"
                ),
                {"tenant": tenant, "app": app},
            )
        with pytest.raises(DBAPIError, match="Remove new collection budgets"):
            with engine.begin() as connection:
                config = configuration()
                config.attributes["connection"] = connection
                command.downgrade(config, "039")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM graph_request_budgets")) == 2
    finally:
        engine.dispose()


async def test_me_navigation_uses_the_same_user_read_fault(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(
            database, settings, tenant, app, user, {"User.Read"}, "delegated"
        )
        fault = await client.post(
            f"/_mock/tenants/{tenant}/graph/faults",
            json={
                "client_id": str(app),
                "resource": "users",
                "kind": "next_read_429",
                "retry_after": 1,
            },
        )
        assert fault.status_code == 201
        denied = await client.get("/v1.0/me/ownedObjects", headers=headers)
        assert denied.status_code == 429, denied.text
        assert denied.headers["Retry-After"] == "1"
        assert (
            await client.get(f"/v1.0/users/{user}/ownedObjects", headers=headers)
        ).status_code == 200


async def test_batch_continuation_reauthorizes_revoked_directory_role(database, settings):
    from urllib.parse import urlsplit

    from sqlalchemy import delete

    from mockazure.identity.models import DirectoryRole

    async with graph_client(database, settings) as (client, tenant, app, user):
        async with database.sessions.begin() as session:
            session.add(DirectoryRole(tenant_id=tenant, user_id=user, role="Global Reader"))
        headers = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {"RoleManagement.Read.Directory", "User.Read"},
            "delegated",
        )
        first = await client.get(
            "/v1.0/roleManagement/directory/roleDefinitions?$select=id&$top=1", headers=headers
        )
        assert first.status_code == 200, first.text
        parts = urlsplit(first.json()["@odata.nextLink"])
        continuation = parts.path + "?" + parts.query
        async with database.sessions.begin() as session:
            await session.execute(
                delete(DirectoryRole).where(
                    DirectoryRole.tenant_id == tenant, DirectoryRole.user_id == user
                )
            )
        batch = await client.post(
            "/v1.0/$batch",
            headers=headers,
            json={
                "requests": [
                    {"id": "revoked-page", "method": "GET", "url": continuation},
                    {"id": "self", "method": "GET", "url": f"/users/{user}?$select=id"},
                ]
            },
        )
        assert batch.status_code == 200, batch.text
        assert {row["id"]: row["status"] for row in batch.json()["responses"]} == {
            "revoked-page": 403,
            "self": 200,
        }

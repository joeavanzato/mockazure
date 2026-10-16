from uuid import uuid4

import pytest
from sqlalchemy import select

from mockazure.directory.models import DirectoryRelation
from mockazure.identity.models import DirectoryRole, User
from mockazure.identity.types import PrincipalContext
from mockazure.providers.graph.errors import GraphError
from tests.integration.graph_support import (
    GRAPH_AUDIENCE,
    graph_client,
    headers_for,
    persisted_group,
    seed_relation_versions,
)


async def test_directory_reads_share_identity(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user_id):
        await seed_relation_versions(database, tenant)
        headers = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        for version in ("v1.0", "beta"):
            collection = await client.get(f"/{version}/users", headers=headers)
            assert collection.status_code == 200, collection.text
            data = collection.json()["value"]
            assert [item["id"] for item in data] == [str(user_id)]
            assert data[0]["displayName"] == "Directory Reader"
            assert "accountEnabled" not in data[0]
            for target in (str(user_id), "reader%40contoso.test"):
                response = await client.get(
                    f"/{version}/users/{target}?$select=id,accountEnabled", headers=headers
                )
                assert response.status_code == 200, response.text
                assert response.json()["accountEnabled"] is True
                assert response.json()["id"] == str(user_id)
            missing = await client.get(f"/{version}/users/{uuid4()}", headers=headers)
            assert missing.status_code == 404
        await seed_relation_versions(database, tenant)
        self_headers = await headers_for(
            database, settings, tenant, app, user_id, {"User.Read"}, "delegated"
        )
        me = await client.get("/v1.0/me", headers=self_headers)
        assert me.json()["id"] == str(user_id)
        assert me.json()["displayName"] == data[0]["displayName"]
        assert "verifier" not in collection.text
        assert "local-directory-password" not in collection.text


async def test_read_permissions_basic_profiles_and_audience(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user_id):
        await seed_relation_versions(database, tenant)
        basic = await headers_for(
            database, settings, tenant, app, user_id, {"User.ReadBasic.All"}, "delegated"
        )
        response = await client.get("/v1.0/users", headers=basic)
        assert response.status_code == 200, response.text
        assert response.json()["value"][0]["displayName"] == "Directory Reader"
        assert "accountEnabled" not in response.json()["value"][0]
        for permissions in (set(), {"User.Read"}, {"Organization.Read.All"}):
            header = await headers_for(
                database, settings, tenant, app, user_id, permissions, "delegated"
            )
            assert (await client.get("/v1.0/users", headers=header)).status_code == 403
        await seed_relation_versions(database, tenant)
        wrong = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {"Directory.Read.All"},
            audience="https://outlook.office365.com",
        )
        assert (await client.get("/v1.0/users", headers=wrong)).status_code == 401
        async with database.sessions.begin() as session:
            row = await session.get(User, user_id)
            row.enabled = False
        assert (await client.get("/v1.0/users", headers=basic)).status_code == 403


async def test_relationships_limit_member_information(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user_id):
        async with database.sessions.begin() as session:
            group = await persisted_group(session, tenant)
            group_id = group.id
            session.add(
                DirectoryRelation(
                    tenant_id=tenant, group_id=group_id, object_id=user_id, relation="member"
                )
            )
        await seed_relation_versions(database, tenant)
        headers = await headers_for(database, settings, tenant, app, None, {"GroupMember.Read.All"})
        response = await client.get(
            f"/v1.0/groups/{group_id}/members?$select=id,displayName", headers=headers
        )
        assert response.status_code == 200, response.text
        member = response.json()["value"][0]
        assert member["id"] == str(user_id)
        assert member["@odata.type"] == "#microsoft.graph.user"
        assert member["displayName"] is None
        await seed_relation_versions(database, tenant)
        full = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        response = await client.get(f"/v1.0/groups/{group_id}/members", headers=full)
        assert response.json()["value"][0]["displayName"] == "Directory Reader"
        groups = await client.get("/v1.0/groups", headers=full)
        assert groups.json()["value"][0]["id"] == str(group_id)


async def test_foreign_user_is_indistinguishable_from_missing(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        other = (
            await client.post(
                "/_mock/tenants",
                json={"slug": "foreign", "display_name": "Foreign", "domain": "foreign.test"},
            )
        ).json()["id"]
        foreign = (
            await client.post(
                f"/_mock/tenants/{other}/identity/users",
                json={
                    "upn": "foreign@foreign.test",
                    "display_name": "Private",
                    "password": "foreign-password",
                },
            )
        ).json()["id"]
        await seed_relation_versions(database, tenant)
        headers = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        first = await client.get(f"/v1.0/users/{foreign}", headers=headers)
        missing = await client.get(f"/v1.0/users/{uuid4()}", headers=headers)
        assert first.status_code == missing.status_code == 404
        assert first.json()["error"]["code"] == missing.json()["error"]["code"]
        assert "Private" not in first.text


async def test_sensitive_permissions_require_both_and_current_role(database, settings):
    from mockazure.providers.graph.permissions import GraphOperation, authorize_directory

    async with graph_client(database, settings) as (_, tenant, app, user_id):
        operation = GraphOperation("v1.0", "users", "PATCH", frozenset({"accountEnabled"}))
        async with database.sessions() as session:
            for permissions in ({"User.Read.All"}, {"User.EnableDisableAccount.All"}):
                context = PrincipalContext(
                    tenant, app, None, "application", GRAPH_AUDIENCE, frozenset(permissions)
                )
                with pytest.raises(GraphError) as denied:
                    await authorize_directory(session, context, operation, user_id)
                assert denied.value.status == 403
            permissions = frozenset({"User.Read.All", "User.EnableDisableAccount.All"})
            context = PrincipalContext(
                tenant, app, None, "application", GRAPH_AUDIENCE, permissions
            )
            await authorize_directory(session, context, operation, user_id)
            delegated = PrincipalContext(
                tenant, app, user_id, "delegated", GRAPH_AUDIENCE, permissions
            )
            with pytest.raises(GraphError) as denied:
                await authorize_directory(session, delegated, operation, uuid4())
            assert denied.value.status == 403
            session.add(DirectoryRole(tenant_id=tenant, user_id=user_id, role="User Administrator"))
            await session.flush()
            # An ordinary other target is permitted; privileged-target handling belongs to Task 5.
            await authorize_directory(session, delegated, operation, uuid4())
            assert await session.scalar(select(DirectoryRole.role)) == "User Administrator"


async def test_graph_permission_catalog_drives_real_oauth_and_me_projection(database, settings):
    async with graph_client(database, settings) as (client, tenant, _, user_id):
        base = f"/_mock/tenants/{tenant}/identity"
        created = await client.post(
            base + "/applications",
            json={
                "display_name": "Reader",
                "client_mode": "confidential",
                "required_permissions": [
                    {"resource": "graph", "kind": "application", "permissions": ["User.Read.All"]}
                ],
            },
        )
        assert created.status_code == 201, created.text
        app = created.json()["client_id"]
        secret = (
            await client.post(base + f"/applications/{app}/credentials", json={"kind": "secret"})
        ).json()["secret"]
        grant = await client.put(
            base + f"/grants/{uuid4()}",
            json={
                "client_id": app,
                "resource": "graph",
                "kind": "application",
                "permissions": ["User.Read.All"],
            },
        )
        assert grant.status_code == 200, grant.text
        token = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": app,
                "client_secret": secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        assert token.status_code == 200, token.text
        result = await client.get(
            "/v1.0/users", headers={"Authorization": "Bearer " + token.json()["access_token"]}
        )
        assert result.status_code == 200
        assert result.json()["value"][0]["id"] == str(user_id)
        async with database.sessions.begin() as session:
            row = await session.get(User, user_id)
            row.profile = {"givenName": "Shared profile"}
        await seed_relation_versions(database, tenant)
        own = await headers_for(
            database, settings, tenant, app, user_id, {"User.Read"}, "delegated"
        )
        me = await client.get("/v1.0/me?$select=id,givenName", headers=own)
        assert me.status_code == 200, me.text
        assert me.json()["givenName"] == "Shared profile"


async def test_operation_matrix_permission_alternatives(database, settings):
    from mockazure.providers.graph.permissions import GraphOperation, authorize_directory

    cases = [
        ("users", "GET", "User.Read.All", set()),
        ("users", "POST", "User.Create", set()),
        ("users", "PATCH", "User.ReadUpdate.All", {"displayName"}),
        ("users", "DELETE", "User.ReadWrite.All", set()),
        ("groups", "GET", "GroupMember.Read.All", set()),
        ("groups", "POST", "Group.ReadWrite.All", set()),
        ("groups", "PATCH", "Group.ReadWrite.All", {"displayName"}),
        ("groups", "DELETE", "Group.ReadWrite.All", set()),
        ("members", "GET", "GroupMember.ReadBasic.All", set()),
        ("transitiveMembers", "GET", "GroupMember.Read.All", set()),
        ("owners", "GET", "GroupMember.Read.All", set()),
        ("members", "POST", "GroupMember.ReadWrite.All", set()),
        ("members", "DELETE", "GroupMember.ReadWrite.All", set()),
        ("memberOf", "GET", "User.Read.All", set()),
        ("transitiveMemberOf", "GET", "User.Read.All", set()),
        ("users.delta", "GET", "User.Read.All", set()),
        ("groups.delta", "GET", "Group.Read.All", set()),
    ]
    async with graph_client(database, settings) as (_, tenant, app, user_id):
        async with database.sessions() as session:
            session.add(
                DirectoryRole(tenant_id=tenant, user_id=user_id, role="Global Administrator")
            )
            await session.flush()
            for kind in ("application", "delegated"):
                for resource, method, permission, fields in cases:
                    operation = GraphOperation("v1.0", resource, method, frozenset(fields))
                    context = PrincipalContext(
                        tenant,
                        app,
                        user_id if kind == "delegated" else None,
                        kind,
                        GRAPH_AUDIENCE,
                        frozenset({permission}),
                    )
                    await authorize_directory(session, context, operation, uuid4())
                    empty = PrincipalContext(
                        tenant, app, context.subject_id, kind, GRAPH_AUDIENCE, frozenset()
                    )
                    with pytest.raises(GraphError) as denied:
                        await authorize_directory(session, empty, operation, uuid4())
                    assert denied.value.status == 403

import asyncio
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from sqlalchemy import select

from mockazure.identity.models import DirectoryRole, User
from tests.integration.graph_support import headers_for
from tests.integration.identity_support import identity_client
from tests.integration.test_identity_authorization import (
    CALLBACK,
    PASSWORD,
    VERIFIER,
    hidden,
    params,
    provision,
    sign_in,
)


async def setup_actor(client, tenant):
    permissions = ["User.Read", "User.RevokeSessions.All"]
    user, app = await provision(client, tenant, permissions)
    result = await client.put(
        f"/_mock/tenants/{tenant}/identity/grants/{uuid4()}",
        json={
            "client_id": app["client_id"],
            "resource": "graph",
            "kind": "delegated",
            "permissions": permissions,
        },
    )
    assert result.status_code == 200, result.text
    return user, app


async def login(client, tenant, app):
    response = await sign_in(
        client,
        tenant,
        app,
        scope="User.Read User.RevokeSessions.All offline_access",
    )
    assert response.status_code == 303, response.text
    code = parse_qs(urlsplit(response.headers["location"]).query)["code"][0]
    response = await client.post(
        f"/{tenant}/oauth2/v2.0/token",
        data={
            "grant_type": "authorization_code",
            "client_id": app["client_id"],
            "redirect_uri": CALLBACK,
            "code": code,
            "code_verifier": VERIFIER,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def refresh(client, tenant, app, tokens):
    return await client.post(
        f"/{tenant}/oauth2/v2.0/token",
        data={
            "grant_type": "refresh_token",
            "client_id": app["client_id"],
            "refresh_token": tokens["refresh_token"],
        },
    )


async def test_revoke_sessions_advances_valid_from_and_requires_new_sign_in(database, settings):
    async with identity_client(database, settings) as (client, tenant, operator, *_):
        user, app = await setup_actor(client, tenant)
        tokens = await login(client, tenant, app)
        headers = {"Authorization": "Bearer " + tokens["access_token"]}
        assert (await client.get("/v1.0/me", headers=headers)).status_code == 200
        assert (await refresh(client, tenant, app, tokens)).status_code == 200
        before = datetime.now(UTC)
        revoked = await client.post("/v1.0/me/revokeSignInSessions", headers=headers)
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["value"] is True
        assert (await refresh(client, tenant, app, tokens)).json()["error"] == "invalid_grant"
        assert (await client.get("/v1.0/me", headers=headers)).status_code == 403
        admin = await headers_for(database, settings, tenant, operator, None, {"User.Read.All"})
        read = await client.get(
            "/v1.0/users/" + user["id"],
            headers=admin,
            params={"$select": "id,signInSessionsValidFromDateTime"},
        )
        assert read.status_code == 200, read.text
        stamp = datetime.fromisoformat(
            read.json()["signInSessionsValidFromDateTime"].replace("Z", "+00:00")
        )
        assert before <= stamp <= datetime.now(UTC)
        new_tokens = await login(client, tenant, app)
        assert (await refresh(client, tenant, app, new_tokens)).status_code == 200


async def test_application_cannot_revoke_me_or_use_delegated_only_higher_permission(
    database, settings
):
    async with identity_client(database, settings) as (client, tenant, operator, *_):
        user, _ = await setup_actor(client, tenant)
        headers = await headers_for(
            database, settings, tenant, operator, None, {"User.RevokeSessions.All"}
        )
        assert (
            await client.post("/v1.0/me/revokeSignInSessions", headers=headers)
        ).status_code == 403
        path = "/v1.0/users/" + user["upn"] + "/revokeSignInSessions"
        result = await client.post(path, headers=headers)
        assert result.status_code == 200 and result.json()["value"] is True
        wrong = await headers_for(
            database, settings, tenant, operator, None, {"User.ReadWrite.All"}
        )
        assert (await client.post(path, headers=wrong)).status_code == 403
        assert (
            await client.post(path, headers=headers, json={"invented": True})
        ).status_code == 400


async def test_external_guest_home_sessions_are_not_claimed_revoked(database, settings):
    async with identity_client(database, settings) as (client, tenant, operator, *_):
        user, app = await setup_actor(client, tenant)
        async with database.sessions.begin() as session:
            row = await session.get(User, UUID(user["id"]))
            row.profile = {**row.profile, "userType": "Guest", "externalUserState": "Accepted"}
        tokens = await login(client, tenant, app)
        headers = await headers_for(
            database, settings, tenant, operator, None, {"User.RevokeSessions.All"}
        )
        result = await client.post(
            "/v1.0/users/" + user["id"] + "/revokeSignInSessions", headers=headers
        )
        assert result.status_code == 200, result.text
        assert (await refresh(client, tenant, app, tokens)).status_code == 200
        async with database.sessions() as session:
            row = await session.scalar(select(User).where(User.id == UUID(user["id"])))
            assert row.sessions_valid_from is None


async def test_leaver_cannot_get_new_or_refreshed_tokens(database, settings):
    async with identity_client(database, settings) as (client, tenant, operator, *_):
        user, app = await setup_actor(client, tenant)
        tokens = await login(client, tenant, app)
        headers = await headers_for(
            database,
            settings,
            tenant,
            operator,
            None,
            {"User.EnableDisableAccount.All", "User.Read.All"},
        )
        response = await client.patch(
            "/v1.0/users/" + user["id"],
            headers=headers,
            json={"accountEnabled": False},
        )
        assert response.status_code == 204, response.text
        assert (await refresh(client, tenant, app, tokens)).json()["error"] == "invalid_grant"
        old = {"Authorization": "Bearer " + tokens["access_token"]}
        assert (await client.get("/v1.0/me", headers=old)).status_code == 403
        started = await client.get(f"/{tenant}/oauth2/v2.0/authorize", params=params(app))
        fields = {key: hidden(started, key) for key in ("transaction", "csrf")}
        denied = await client.post(
            "/login",
            data={**fields, "username": user["upn"], "password": PASSWORD},
        )
        assert denied.status_code == 400 and denied.json()["error"] == "access_denied"


async def test_revocation_invalidates_unredeemed_code_and_concurrent_refresh(database, settings):
    async with identity_client(database, settings) as (client, tenant, operator, *_):
        user, app = await setup_actor(client, tenant)
        tokens = await login(client, tenant, app)
        pending = await sign_in(
            client, tenant, app, scope="User.Read User.RevokeSessions.All offline_access"
        )
        code = parse_qs(urlsplit(pending.headers["location"]).query)["code"][0]
        admin = await headers_for(
            database, settings, tenant, operator, None, {"User.RevokeSessions.All"}
        )
        revoked, racing = await asyncio.wait_for(
            asyncio.gather(
                client.post(f"/v1.0/users/{user['id']}/revokeSignInSessions", headers=admin),
                refresh(client, tenant, app, tokens),
            ),
            timeout=15,
        )
        assert revoked.status_code == 200, revoked.text
        assert racing.status_code in {200, 400}, racing.text
        if racing.status_code == 200:
            assert (await refresh(client, tenant, app, racing.json())).json()["error"] == (
                "invalid_grant"
            )
        result = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "authorization_code",
                "client_id": app["client_id"],
                "redirect_uri": CALLBACK,
                "code": code,
                "code_verifier": VERIFIER,
            },
        )
        assert result.status_code == 400 and result.json()["error"] == "invalid_grant"


async def test_delegated_revocation_obeys_privileged_target_hierarchy(database, settings):
    async with identity_client(database, settings) as (client, tenant, operator, *_):
        user, app = await setup_actor(client, tenant)
        target = await client.post(
            f"/_mock/tenants/{tenant}/identity/users",
            json={
                "upn": "admin@contoso.test",
                "display_name": "Admin",
                "password": "Local admin test password!",
            },
        )
        target_id = UUID(target.json()["id"])
        async with database.sessions.begin() as session:
            session.add_all(
                [
                    DirectoryRole(
                        tenant_id=UUID(tenant),
                        user_id=UUID(user["id"]),
                        role="Authentication Administrator",
                    ),
                    DirectoryRole(
                        tenant_id=UUID(tenant), user_id=target_id, role="Global Administrator"
                    ),
                ]
            )
        tokens = await login(client, tenant, app)
        actor = {"Authorization": "Bearer " + tokens["access_token"]}
        result = await client.post(f"/v1.0/users/{target_id}/revokeSignInSessions", headers=actor)
        assert result.status_code == 403, result.text
        async with database.sessions() as session:
            assert (await session.get(User, target_id)).sessions_valid_from is None
        assert (await refresh(client, tenant, app, tokens)).status_code == 200

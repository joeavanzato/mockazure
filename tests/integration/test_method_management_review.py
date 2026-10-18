import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tests.integration.graph_support import headers_for
from tests.integration.identity_support import identity_client
from tests.integration.test_identity_authorization import PASSWORD, hidden, params, provision
from tests.integration.test_password_reset import PASSWORD_ID, TEMP, drain_reset, redeem

EMAIL = "3ddfcfc8-9383-446f-83cc-3ab9be4be18f"
SCOPE = "UserAuthenticationMethod.ReadWrite"


async def setup(client, tenant):
    user, app = await provision(client, tenant, [SCOPE, "User.Read"])
    response = await client.post(
        f"/_mock/tenants/{tenant}/security/users/{user['id']}/authenticationMethods",
        json={
            "@odata.type": "#microsoft.graph.emailAuthenticationMethod",
            "emailAddress": "recover@example.test",
        },
    )
    assert response.status_code == 201, response.text
    grant = await client.put(
        f"/_mock/tenants/{tenant}/identity/grants/{uuid4()}",
        json={
            "client_id": app["client_id"],
            "resource": "graph",
            "kind": "delegated",
            "permissions": [SCOPE, "User.Read"],
        },
    )
    assert grant.status_code == 200, grant.text
    return user, app


async def login_start(client, tenant, user, app):
    response = await client.get(f"/{tenant}/oauth2/v2.0/authorize", params=params(app, scope=SCOPE))
    assert response.status_code == 200, response.text
    fields = {key: hidden(response, key) for key in ("transaction", "csrf")}
    response = await client.post(
        "/login", data={**fields, "username": user["upn"], "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return fields


async def finish_login(client, tenant, app, fields):
    response = await client.post("/consent", data={**fields, "decision": "approve"})
    assert response.status_code == 200, response.text
    callback = await client.post("/mfa", data={**fields, "decision": "skip"})
    assert callback.status_code == 303, callback.text
    tokens = await redeem(client, tenant, app, callback)
    assert tokens.status_code == 200, tokens.text
    return {"Authorization": "Bearer " + tokens.json()["access_token"]}


async def test_stale_password_authentication_cannot_delete(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        user, app = await setup(client, tenant)
        old = datetime.now(UTC) - timedelta(minutes=11)
        client._transport.app.state.clock = lambda: old
        fields = await login_start(client, tenant, user, app)
        client._transport.app.state.clock = lambda: datetime.now(UTC)
        auth = await finish_login(client, tenant, app, fields)
        result = await client.delete("/v1.0/me/authentication/emailMethods/" + EMAIL, headers=auth)
        assert result.status_code == 403, (
            f"11-minute-old password authentication accepted: {result.status_code}"
        )


async def test_reset_committed_before_locked_check_rejects_self_delete(
    database, settings, monkeypatch
):
    from mockazure.providers.graph.security import method_routes

    async with identity_client(database, settings) as (client, tenant, *_):
        user, app = await setup(client, tenant)
        fields = await login_start(client, tenant, user, app)
        own = await finish_login(client, tenant, app, fields)
        admin = await client.post(
            f"/_mock/tenants/{tenant}/identity/users",
            json={"upn": "reset@contoso.test", "display_name": "Reset", "password": PASSWORD},
        )
        actor = admin.json()["id"]
        assert (
            await client.put(
                f"/_mock/tenants/{tenant}/identity/users/{actor}/roles/"
                "Privileged Authentication Administrator"
            )
        ).status_code == 200
        auth = await headers_for(
            database,
            settings,
            tenant,
            app["client_id"],
            actor,
            {"UserAuthenticationMethod.ReadWrite.All"},
            "delegated",
        )
        reached, release = asyncio.Event(), asyncio.Event()
        original = method_routes.method_user

        async def paused_user(*args):
            row = await original(*args)
            reached.set()
            await release.wait()
            return row

        monkeypatch.setattr(method_routes, "method_user", paused_user)
        deletion = asyncio.create_task(
            client.delete("/v1.0/me/authentication/emailMethods/" + EMAIL, headers=own)
        )
        await asyncio.wait_for(reached.wait(), 10)
        path = f"/v1.0/users/{user['id']}/authentication/methods/{PASSWORD_ID}/resetPassword"
        try:
            result = await client.post(path, json={"newPassword": TEMP}, headers=auth)
            assert result.status_code == 202, result.text
            await drain_reset(database, tenant)
        finally:
            release.set()
        result = await asyncio.wait_for(deletion, 10)
        assert result.status_code == 403, (
            f"Pre-reset cached identity accepted after reset commit: {result.status_code}"
        )


async def test_device_redemption_and_refresh_preserve_authentication_time(database, settings):
    import jwt

    from tests.integration.test_identity_sessions import browser_device

    async with identity_client(database, settings) as (client, tenant, *_):
        user, app = await setup(client, tenant)
        stamp = datetime.now(UTC) - timedelta(minutes=11)
        client._transport.app.state.clock = lambda: stamp
        endpoint = f"/{tenant}/oauth2/v2.0"
        started = await client.post(
            endpoint + "/devicecode",
            data={"client_id": app["client_id"], "scope": SCOPE + " openid offline_access"},
        )
        assert started.status_code == 200, started.text
        device = started.json()
        await browser_device(client, device)
        client._transport.app.state.clock = lambda: datetime.now(UTC)
        issued = await client.post(
            endpoint + "/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": app["client_id"],
                "device_code": device["device_code"],
            },
        )
        assert issued.status_code == 200, issued.text
        original = issued.json()
        refreshed = await client.post(
            endpoint + "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": app["client_id"],
                "refresh_token": original["refresh_token"],
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        for tokens in (original, refreshed.json()):
            claims = jwt.decode(tokens["id_token"], options={"verify_signature": False})
            assert claims["auth_time"] == int(stamp.timestamp())
            result = await client.delete(
                "/v1.0/me/authentication/emailMethods/" + EMAIL,
                headers={"Authorization": "Bearer " + tokens["access_token"]},
            )
            assert result.status_code == 403, result.text

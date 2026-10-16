import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import jwt
from sqlalchemy import select

from tests.integration.identity_support import identity_client
from tests.integration.test_identity_authorization import (
    CALLBACK,
    PASSWORD,
    VERIFIER,
    hidden,
    provision,
    sign_in,
)


async def browser_device(client, device, *, decision="approve"):
    response = await client.get("/device", params={"user_code": device["user_code"]})
    assert response.status_code == 200, response.text
    fields = {name: hidden(response, name) for name in ("transaction", "csrf")}
    response = await client.post(
        "/login", data={**fields, "username": "reader@contoso.test", "password": PASSWORD}
    )
    assert response.status_code == 200
    response = await client.post("/consent", data={**fields, "decision": decision})
    if decision == "approve":
        assert response.status_code == 200
        response = await client.post("/mfa", data={**fields, "decision": "complete"})
    assert response.status_code == 200


async def test_device_polling_state_persists(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        user, app = await provision(client, tenant)
        now = datetime.now(UTC)
        client._transport.app.state.clock = lambda: now
        endpoint = f"/{tenant}/oauth2/v2.0"
        response = await client.post(
            endpoint + "/devicecode",
            data={"client_id": app["client_id"], "scope": "User.Read offline_access"},
        )
        assert response.status_code == 200, response.text
        device = response.json()
        assert device["interval"] == 5 and device["expires_in"] == 900
        fields = {
            "client_id": app["client_id"],
            "device_code": device["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        assert (await client.post(endpoint + "/token", data=fields)).json()["error"] == (
            "authorization_pending"
        )
        assert (await client.post(endpoint + "/token", data=fields)).json()["error"] == "slow_down"
        await browser_device(client, device)
        now += timedelta(seconds=10)
        results = await asyncio.gather(
            *(client.post(endpoint + "/token", data=fields) for _ in range(2))
        )
        assert sorted(r.status_code for r in results) == [200, 400]
        assert next(r.json() for r in results if r.status_code == 400)["error"] == "invalid_grant"
        tokens = next(r.json() for r in results if r.status_code == 200)
        assert (
            jwt.decode(tokens["access_token"], options={"verify_signature": False})["oid"]
            == (user["id"])
        )
        assert "refresh_token" in tokens


async def test_device_denial_expiry_and_client_binding(database, settings):
    async with identity_client(database, settings) as (client, tenant, other_client, *_):
        _, app = await provision(client, tenant)
        now = datetime.now(UTC)
        client._transport.app.state.clock = lambda: now
        endpoint = f"/{tenant}/oauth2/v2.0"

        async def start():
            return (
                await client.post(
                    endpoint + "/devicecode",
                    data={"client_id": app["client_id"], "scope": "User.Read"},
                )
            ).json()

        device = await start()
        fields = {
            "client_id": app["client_id"],
            "device_code": device["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        wrong = await client.post(endpoint + "/token", data={**fields, "client_id": other_client})
        assert wrong.status_code in (400, 401)
        await browser_device(client, device, decision="deny")
        assert (await client.post(endpoint + "/token", data=fields)).json()["error"] == (
            "authorization_declined"
        )
        device = await start()
        now += timedelta(seconds=901)
        fields["device_code"] = device["device_code"]
        assert (await client.post(endpoint + "/token", data=fields)).json()[
            "error"
        ] == "expired_token"


async def test_refresh_absolute_expiry_concurrency_grants_and_revocation(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        _, app = await provision(client, tenant)
        response = await sign_in(client, tenant, app, scope="openid User.Read offline_access")
        code = parse_qs(urlsplit(response.headers["location"]).query)["code"][0]
        endpoint = f"/{tenant}/oauth2/v2.0/token"
        tokens = (
            await client.post(
                endpoint,
                data={
                    "grant_type": "authorization_code",
                    "client_id": app["client_id"],
                    "redirect_uri": CALLBACK,
                    "code": code,
                    "code_verifier": VERIFIER,
                },
            )
        ).json()
        fields = {
            "grant_type": "refresh_token",
            "client_id": app["client_id"],
            "refresh_token": tokens["refresh_token"],
        }
        results = await asyncio.gather(*(client.post(endpoint, data=fields) for _ in range(2)))
        assert all(r.status_code == 200 for r in results), [r.text for r in results]
        from mockazure.oauth.models import LoginSession

        async with database.sessions() as session:
            stored = await session.scalar(select(LoginSession))
            deadline, session_id = stored.expires_at, stored.id
        extra = await client.post(endpoint, data={**fields, "scope": "Directory.Read.All"})
        assert extra.json()["error"] == "invalid_scope"
        base = f"/_mock/tenants/{tenant}/identity/sessions"
        listed = await client.get(base)
        assert listed.status_code == 200 and len(listed.json()["items"]) == 1
        assert tokens["refresh_token"] not in listed.text
        assert (await client.delete(base + "/" + str(session_id))).status_code == 200
        for token in [tokens, *(r.json() for r in results)]:
            assert (
                await client.post(
                    endpoint, data={**fields, "refresh_token": token["refresh_token"]}
                )
            ).json()["error"] == "invalid_grant"
        async with database.sessions() as session:
            stored = await session.get(LoginSession, session_id)
            assert stored.expires_at == deadline and stored.revoked_at is not None

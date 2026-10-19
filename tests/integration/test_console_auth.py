from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import update

from mockazure.admin.app import create_app
from mockazure.console.models import ConsoleSession
from mockazure.console.sessions import set_password

PASSWORD = "local lab password for testing"
ORIGIN = "https://localhost:8443"


def browser(database, settings, source="127.0.0.1"):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings, database), client=(source, 1234)),
        base_url=ORIGIN,
    )


async def test_console_login_csrf_logout_and_bearer_compatibility(database, settings):
    settings.token_file.write_text("A" * 43)
    await set_password(database, PASSWORD)
    async with browser(database, settings) as client:
        response = await client.post(
            "/_mock/console/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
        )
        assert response.status_code == 200, response.text
        cookie = response.headers["set-cookie"].lower()
        assert all(
            flag in cookie for flag in ("secure", "httponly", "samesite=strict", "path=/_mock")
        )
        csrf = response.json()["csrf_token"]
        assert (await client.get("/_mock/tenants")).status_code == 200
        assert (await client.get("/_mock/console/session")).json()["csrf_token"] == csrf
        body = {"slug": "browser", "display_name": "Browser", "domain": "browser.test"}
        assert (await client.post("/_mock/tenants", json=body)).status_code == 403
        created = await client.post(
            "/_mock/tenants", json=body, headers={"Origin": ORIGIN, "X-MockAzure-CSRF": csrf}
        )
        assert created.status_code == 201
        assert (
            await client.get("/_mock/tenants", headers={"Authorization": "Bearer invalid"})
        ).status_code == 401
        assert (await client.get("/v1.0/users")).status_code == 401
        assert (await client.get("/subscriptions?api-version=2021-01-01")).status_code == 401
        assert (
            await client.post(
                "/_mock/console/logout", headers={"Origin": ORIGIN, "X-MockAzure-CSRF": csrf}
            )
        ).status_code == 200
        assert (await client.get("/_mock/tenants")).status_code == 401
        assert (
            await client.get("/_mock/tenants", headers={"Authorization": "Bearer " + "A" * 43})
        ).status_code == 200


async def test_expiry_rotation_cross_origin_and_replica_sessions(database, settings):
    await set_password(database, PASSWORD)
    async with browser(database, settings) as first, browser(database, settings) as second:
        rejected = await first.post(
            "/_mock/console/login",
            json={"password": PASSWORD},
            headers={"Origin": "https://evil.test"},
        )
        assert rejected.status_code == 403
        invalid_host = await first.get("/_mock/ui/", headers={"Host": "evil.test"})
        assert invalid_host.status_code == 400
        await first.post(
            "/_mock/console/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
        )
        second.cookies.update(first.cookies)
        assert (await second.get("/_mock/tenants")).status_code == 200
        async with database.sessions.begin() as session:
            await session.execute(update(ConsoleSession).values(expires_at=datetime.now(UTC)))
        assert (await second.get("/_mock/tenants")).status_code == 401
        await first.post(
            "/_mock/console/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
        )
        await set_password(database, PASSWORD + " rotated")
        assert (await first.get("/_mock/tenants")).status_code == 401


async def test_failed_login_throttle_is_shared_and_recovers(database, settings):
    from mockazure.console.models import LoginFailure

    await set_password(database, PASSWORD)
    async with browser(database, settings) as first, browser(database, settings) as second:
        for index in range(5):
            client = first if index % 2 else second
            response = await client.post(
                "/_mock/console/login",
                json={"password": "wrong but long password"},
                headers={"Origin": ORIGIN},
            )
            assert response.status_code == 401
        response = await second.post(
            "/_mock/console/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
        )
        assert response.status_code == 429
        assert int(response.headers["retry-after"]) > 0
        async with database.sessions.begin() as session:
            await session.execute(
                update(LoginFailure).values(occurred_at=datetime.now(UTC) - timedelta(minutes=2))
            )
        assert (
            await first.post(
                "/_mock/console/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
            )
        ).status_code == 200


async def test_global_login_throttle_and_forwarded_source_not_trusted(database, settings):
    await set_password(database, PASSWORD)
    for index in range(30):
        async with browser(database, settings, f"192.168.1.{index + 1}") as client:
            response = await client.post(
                "/_mock/console/login",
                json={"password": "wrong long password"},
                headers={"Origin": ORIGIN},
            )
            assert response.status_code == 401
    async with browser(database, settings, "192.168.1.99") as client:
        assert (
            await client.post(
                "/_mock/console/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
            )
        ).status_code == 429
    await set_password(database, PASSWORD)
    async with browser(database, settings) as client:
        for index in range(5):
            assert (
                await client.post(
                    "/_mock/console/login",
                    json={"password": "wrong long password"},
                    headers={"Origin": ORIGIN, "X-Forwarded-For": f"192.168.2.{index}"},
                )
            ).status_code == 401
        assert (
            await client.post(
                "/_mock/console/login",
                json={"password": PASSWORD},
                headers={"Origin": ORIGIN, "X-Forwarded-For": "192.168.2.99"},
            )
        ).status_code == 429


async def test_cookie_does_not_authorize_storage_or_arm(database, settings):
    from tests.integration.graph_support import graph_client
    from tests.integration.test_storage_authorization import provision

    async with graph_client(database, settings) as (admin, tenant, app, *_):
        _, _, endpoints, _ = await provision(admin, database, settings, tenant, app)
    await set_password(database, PASSWORD)
    async with browser(database, settings) as client:
        await client.post(
            "/_mock/console/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
        )
        cookie = client.cookies.get("mockazure_lab")
        headers = {"Cookie": "mockazure_lab=" + cookie}
        assert (
            await client.get("/subscriptions?api-version=2021-01-01", headers=headers)
        ).status_code == 401
        response = await client.get(
            endpoints["blob"],
            params={"comp": "list"},
            headers={**headers, "x-ms-version": "2025-11-05"},
        )
        assert response.status_code == 403, response.text


async def test_console_locked_disabled_and_replica_logout(database, settings):
    async with browser(database, settings) as first, browser(database, settings) as second:
        assert (
            await first.post(
                "/_mock/console/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
            )
        ).json()["error"]["code"] == "ConsoleLocked"
        assert (await first.get("/_mock/tenants")).status_code == 401
        await set_password(database, PASSWORD)
        response = await first.post(
            "/_mock/console/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
        )
        second.cookies.update(first.cookies)
        assert (
            await second.post(
                "/_mock/console/logout",
                headers={"Origin": ORIGIN, "X-MockAzure-CSRF": response.json()["csrf_token"]},
            )
        ).status_code == 200
        assert (await first.get("/_mock/tenants")).status_code == 401
        settings.console_enabled = False
        assert (await first.get("/_mock/ui/")).status_code == 404

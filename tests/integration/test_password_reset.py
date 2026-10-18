import asyncio
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from mockazure.admin.app import create_app
from mockazure.db.job_model import Job
from mockazure.identity.credentials import verify_secret
from mockazure.identity.models import User
from mockazure.jobs.dispatch import dispatch_job
from mockazure.jobs.repository import claim_job
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

PASSWORD_ID = "28c10230-6103-485e-b985-444c60001490"
TEMP = "Temporary local password 62!"
FINAL = "Final local password 83!"


async def setup_reset(client, database, settings, tenant):
    user, app = await provision(client, tenant)
    admin = await client.post(
        f"/_mock/tenants/{tenant}/identity/users",
        json={
            "upn": "reset-admin@contoso.test",
            "display_name": "Reset admin",
            "password": PASSWORD,
        },
    )
    assert admin.status_code == 201
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
    path = f"/v1.0/users/{user['id']}/authentication/methods/{PASSWORD_ID}/resetPassword"
    return user, app, actor, auth, path


async def drain_reset(database, tenant):
    now = datetime.now(UTC)
    async with database.sessions.begin() as session:
        claim = await claim_job(
            session,
            owner=uuid4(),
            now=now,
            lease_seconds=60,
            tenant_id=UUID(str(tenant)),
            kind="authentication.passwordReset",
        )
    assert claim is not None
    result = await dispatch_job(database, claim, now)
    assert result.state == "succeeded", result
    return claim


async def redeem(client, tenant, app, callback):
    return await client.post(
        f"/{tenant}/oauth2/v2.0/token",
        data={
            "grant_type": "authorization_code",
            "client_id": app["client_id"],
            "redirect_uri": CALLBACK,
            "code": parse_qs(urlsplit(callback.headers["location"]).query)["code"][0],
            "code_verifier": VERIFIER,
        },
    )


async def test_password_reset_operation_restart(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        user, app, actor, auth, path = await setup_reset(client, database, settings, tenant)
        result = await client.post(path, json={}, headers=auth)
        assert result.status_code == 202, result.text
        password = result.json()["newPassword"]
        assert len(password) >= 24 and result.headers["location"].startswith("https://localhost")
        location = result.headers["location"]
        assert (await client.get(location, headers=auth)).json()["status"] == "notStarted"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings, database)),
            base_url="https://localhost:8443",
        ) as replica:
            claim = await drain_reset(database, tenant)
            status = await replica.get(location, headers=auth)
            assert status.status_code == 200 and status.json()["status"] == "succeeded", status.text
            assert password not in status.text and "verifier" not in status.text
        async with database.sessions.begin() as session:
            stored = await session.get(User, UUID(user["id"]))
            assert verify_secret(password, stored.verifier) and not verify_secret(
                PASSWORD, stored.verifier
            )
            assert stored.password_change_required
            epoch = stored.credential_version
            # A retry after a committed operation cannot reset the password a second time.
            from mockazure.security.password_reset import finish_password_reset

            await finish_password_reset(session, UUID(location.rsplit("/", 1)[1]))
            assert stored.credential_version == epoch
            jobs = list(await session.scalars(select(Job).where(Job.tenant_id == UUID(tenant))))
            assert all(password not in str(row.payload) + str(row.result) for row in jobs)
        assert (await dispatch_job(database, claim, datetime.now(UTC))).state == "stale"


async def test_password_reset_forces_change(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        user, app, actor, auth, path = await setup_reset(client, database, settings, tenant)
        old_code = await sign_in(client, tenant, app, scope="User.Read offline_access")
        tokens = await redeem(client, tenant, app, old_code)
        assert tokens.status_code == 200
        old_access = {"Authorization": "Bearer " + tokens.json()["access_token"]}
        pending_code = await sign_in(client, tenant, app)
        result = await client.post(path, json={"newPassword": TEMP}, headers=auth)
        assert result.status_code == 202 and not result.content, result.text
        await drain_reset(database, tenant)
        assert (await client.get("/v1.0/me", headers=old_access)).status_code == 403
        assert (await redeem(client, tenant, app, pending_code)).status_code == 400
        refresh = {
            "grant_type": "refresh_token",
            "client_id": app["client_id"],
            "refresh_token": tokens.json()["refresh_token"],
        }
        assert (await client.post(f"/{tenant}/oauth2/v2.0/token", data=refresh)).status_code == 400
        begin = await client.get(f"/{tenant}/oauth2/v2.0/authorize", params=params(app))
        fields = {key: hidden(begin, key) for key in ("transaction", "csrf")}
        assert (
            await client.post(
                "/login", data={**fields, "username": user["upn"], "password": PASSWORD}
            )
        ).status_code == 400
        login = await client.post(
            "/login", data={**fields, "username": user["upn"], "password": TEMP}
        )
        assert login.status_code == 200 and 'action="/password-change"' in login.text, login.text
        assert (
            await client.post("/consent", data={**fields, "decision": "approve"})
        ).status_code == 400
        assert (
            await client.post(
                "/password-change",
                data={**fields, "csrf": "wrong", "new_password": FINAL, "confirm_password": FINAL},
            )
        ).status_code == 400
        changed = await client.post(
            "/password-change", data={**fields, "new_password": FINAL, "confirm_password": FINAL}
        )
        assert changed.status_code == 200 and 'action="/consent"' in changed.text, changed.text
        assert (
            await client.post(
                "/password-change",
                data={**fields, "new_password": FINAL, "confirm_password": FINAL},
            )
        ).status_code == 400
        assert (
            await client.post("/consent", data={**fields, "decision": "approve"})
        ).status_code == 200
        callback = await client.post("/mfa", data={**fields, "decision": "skip"})
        assert callback.status_code == 303, callback.text
        assert (await redeem(client, tenant, app, callback)).status_code == 200
        assert (await client.post(f"/{tenant}/oauth2/v2.0/token", data=refresh)).status_code == 400
        assert (await client.get("/v1.0/me", headers=old_access)).status_code == 403
        async with database.sessions() as session:
            stored = await session.get(User, UUID(user["id"]))
            assert not stored.password_change_required
            assert verify_secret(FINAL, stored.verifier) and not verify_secret(
                TEMP, stored.verifier
            )


async def test_reset_rejects_app_only_and_self(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        user, app, actor, auth, path = await setup_reset(client, database, settings, tenant)
        for subject, kind in ((None, "application"), (user["id"], "delegated")):
            denied = await headers_for(
                database,
                settings,
                tenant,
                app["client_id"],
                subject,
                {"UserAuthenticationMethod.ReadWrite.All"},
                kind,
            )
            result = await client.post(path, json={"newPassword": TEMP}, headers=denied)
            assert result.status_code == 403, result.text
        async with database.sessions.begin() as session:
            row = await session.get(User, UUID(user["id"]))
            row.profile = {"onPremisesSyncEnabled": True}
        assert (await client.post(path, json={}, headers=auth)).status_code == 501


async def test_reset_concurrency_and_invalid_passwords(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        user, app, actor, auth, path = await setup_reset(client, database, settings, tenant)
        for invalid in ("short", "x" * 4097, 42, [], "bad\x00password-long"):
            result = await client.post(path, json={"newPassword": invalid}, headers=auth)
            assert result.status_code == 400, result.text
        results = await asyncio.gather(
            *(client.post(path, json={"newPassword": TEMP}, headers=auth) for _ in range(2))
        )
        assert sorted(r.status_code for r in results) == [202, 409]


async def test_reset_additional_admin_roles_are_configurable(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        user, app, actor, auth, path = await setup_reset(client, database, settings, tenant)
        root = f"/_mock/tenants/{tenant}/identity/users/{actor}/roles/"
        assert (
            await client.delete(root + "Privileged Authentication Administrator")
        ).status_code == 200
        for role in ("Password Administrator", "Helpdesk Administrator", "User Administrator"):
            response = await client.put(root + role)
            assert response.status_code == 200, response.text
            result = await client.post(path, json={}, headers=auth)
            assert result.status_code == 202, result.text
            assert (await client.get(result.headers["location"], headers=auth)).status_code == 200
            await drain_reset(database, tenant)
            assert (await client.delete(root + role)).status_code == 200


async def test_reset_rejects_pre_reset_device_approval(database, settings):
    from datetime import timedelta

    from tests.integration.test_identity_sessions import browser_device

    async with identity_client(database, settings) as (client, tenant, *_):
        user, app, actor, auth, path = await setup_reset(client, database, settings, tenant)
        now = datetime.now(UTC)
        client._transport.app.state.clock = lambda: now
        endpoint = f"/{tenant}/oauth2/v2.0"
        device = (
            await client.post(
                endpoint + "/devicecode",
                data={"client_id": app["client_id"], "scope": "User.Read offline_access"},
            )
        ).json()
        await browser_device(client, device)
        assert (
            await client.post(path, json={"newPassword": TEMP}, headers=auth)
        ).status_code == 202
        await drain_reset(database, tenant)
        now += timedelta(seconds=6)
        response = await client.post(
            endpoint + "/token",
            data={
                "client_id": app["client_id"],
                "device_code": device["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        assert response.status_code == 400 and response.json()["error"] == "invalid_grant"


async def test_reset_operation_access_and_lease_recovery(database, settings):
    from datetime import timedelta

    from mockazure.security.reset_model import PasswordResetOperation

    async with identity_client(database, settings) as (client, tenant, *_):
        user, app, actor, auth, path = await setup_reset(client, database, settings, tenant)
        result = await client.post(path, json={"newPassword": TEMP}, headers=auth)
        assert result.status_code == 202
        location = result.headers["location"]
        application = await headers_for(
            database,
            settings,
            tenant,
            app["client_id"],
            None,
            {"UserAuthenticationMethod.ReadWrite.All"},
        )
        assert (await client.get(location, headers=application)).status_code == 403
        assert (
            await client.get(location.replace(user["id"], actor), headers=auth)
        ).status_code == 404
        denied = await headers_for(
            database, settings, tenant, app["client_id"], actor, {"User.Read"}, "delegated"
        )
        assert (await client.get(location, headers=denied)).status_code == 403
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            stale = await claim_job(
                session,
                owner=uuid4(),
                now=now,
                lease_seconds=1,
                tenant_id=UUID(tenant),
                kind="authentication.passwordReset",
            )
        async with database.sessions.begin() as session:
            current = await claim_job(
                session,
                owner=uuid4(),
                now=now + timedelta(seconds=2),
                lease_seconds=60,
                tenant_id=UUID(tenant),
                kind="authentication.passwordReset",
            )
        assert (await dispatch_job(database, stale, now + timedelta(seconds=2))).state == "stale"
        assert (
            await dispatch_job(database, current, now + timedelta(seconds=2))
        ).state == "succeeded"
        status = await client.get(location + "?$select=id,status", headers=auth)
        assert status.json() == {"id": location.rsplit("/", 1)[1], "status": "succeeded"}
        async with database.sessions() as session:
            row = await session.get(PasswordResetOperation, UUID(status.json()["id"]))
            assert row.verifier is None


async def test_original_powershell_qualified_reset_action(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        user, app, actor, auth, path = await setup_reset(client, database, settings, tenant)
        path = path.replace("/resetPassword", "/microsoft.graph.resetPassword")
        result = await client.post(path, json={"newPassword": TEMP}, headers=auth)
        assert result.status_code == 202 and not result.content, result.text
        await drain_reset(database, tenant)
        assert (await client.get(result.headers["location"], headers=auth)).json()[
            "status"
        ] == "succeeded"

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from mockazure.evidence.models import EvidenceObservation, EvidenceSource
from mockazure.identity.models import Grant, User
from mockazure.oauth.models import LoginSession, RefreshHandle
from mockazure.providers.graph.schema_reference import validate_payload
from tests.integration.identity_support import identity_client
from tests.integration.test_evidence_directory import drain
from tests.integration.test_identity_authorization import (
    CALLBACK,
    PASSWORD,
    VERIFIER,
    hidden,
    params,
    provision,
    sign_in,
)
from tests.integration.test_identity_sessions import browser_device


async def records(database):
    await drain(database)
    async with database.sessions() as session:
        return list(
            await session.scalars(
                select(EvidenceObservation.payload)
                .where(EvidenceObservation.family == "signIn")
                .order_by(EvidenceObservation.occurred_at)
            )
        )


async def browser_start(client, tenant, app):
    response = await client.get(f"/{tenant}/oauth2/v2.0/authorize", params=params(app))
    assert response.status_code == 200, response.text
    return {name: hidden(response, name) for name in ("transaction", "csrf")}


async def counts(database):
    async with database.sessions() as session:
        return tuple(
            [
                await session.scalar(select(func.count()).select_from(model))
                for model in (LoginSession, Grant, RefreshHandle)
            ]
        )


async def test_live_auth_types_and_single_device_success(database, settings):
    async with identity_client(database, settings) as (client, tenant, app_id, secret, *_):
        user, app = await provision(client, tenant)
        endpoint = f"/{tenant}/oauth2/v2.0/token"
        callback = await sign_in(
            client, tenant, app, mfa="complete", scope="User.Read offline_access"
        )
        fields = {
            "grant_type": "authorization_code",
            "client_id": app["client_id"],
            "redirect_uri": CALLBACK,
            "code_verifier": VERIFIER,
            "code": parse_qs(urlsplit(callback.headers["location"]).query)["code"][0],
        }
        response = await client.post(endpoint, data=fields)
        assert response.status_code == 200
        refresh = response.json()["refresh_token"]
        assert (await client.post(endpoint, data=fields)).status_code == 400
        assert (
            await client.post(
                endpoint,
                data={
                    "grant_type": "refresh_token",
                    "client_id": app["client_id"],
                    "refresh_token": refresh,
                },
            )
        ).status_code == 200
        assert (
            await client.post(
                endpoint,
                data={
                    "grant_type": "client_credentials",
                    "client_id": app_id,
                    "client_secret": secret["secret"],
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
        ).status_code == 200
        device = (
            await client.post(
                f"/{tenant}/oauth2/v2.0/devicecode",
                data={"client_id": app["client_id"], "scope": "User.Read"},
            )
        ).json()
        await browser_device(client, device)
        fields = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": app["client_id"],
            "device_code": device["device_code"],
        }
        assert (await client.post(endpoint, data=fields)).status_code == 200
        assert (await client.post(endpoint, data=fields)).status_code == 400
    values = await records(database)
    assert [v["signInEventTypes"] for v in values] == [
        ["interactiveUser"],
        ["nonInteractiveUser"],
        ["servicePrincipal"],
        ["interactiveUser"],
    ]
    assert all(v["status"]["errorCode"] == 0 for v in values)
    assert [v["clientAppUsed"] for v in values] == [
        "Mobile Apps and Desktop clients",
        "Mobile Apps and Desktop clients",
        None,
        "Mobile Apps and Desktop clients",
    ]
    assert values[0]["authenticationDetails"][0]["authenticationStepDateTime"] is None
    assert values[2]["userId"] == "" and values[2]["appId"] == app_id
    assert (
        values[0]["userId"] == user["id"]
        and values[0]["userPrincipalName"] == "reader@contoso.test"
    )
    for value in values:
        validate_payload("beta", "signIn", value)
    async with database.sessions() as session:
        source_values = list(await session.scalars(select(EvidenceSource.facts)))
    serialized = json.dumps(source_values)
    assert (
        secret["secret"] not in serialized
        and refresh not in serialized
        and PASSWORD not in serialized
    )


async def test_denial_survives_rollback_without_oauth_state(database, settings):
    async with identity_client(database, settings) as (client, tenant, client_id, secret, *_):
        user, app = await provision(client, tenant)
        fields = await browser_start(client, tenant, app)
        baseline = await counts(database)
        response = await client.post(
            "/login", data={**fields, "username": "reader@contoso.test", "password": "wrong"}
        )
        assert response.status_code == 400 and await counts(database) == baseline
        async with database.sessions.begin() as session:
            (await session.get(User, UUID(user["id"]))).enabled = False
        response = await client.post(
            "/login", data={**fields, "username": "reader@contoso.test", "password": PASSWORD}
        )
        assert response.status_code == 400 and await counts(database) == baseline
        async with database.sessions.begin() as session:
            (await session.get(User, UUID(user["id"]))).enabled = True
        assert (await sign_in(client, tenant, app, consent="deny")).status_code == 303
        # Preserve prior consent; terminal MFA denial creates no additional grant or session.
        fields = await browser_start(client, tenant, app)
        assert (
            await client.post(
                "/login", data={**fields, "username": "reader@contoso.test", "password": PASSWORD}
            )
        ).status_code == 200
        assert (
            await client.post("/consent", data={**fields, "decision": "approve"})
        ).status_code == 200
        before_mfa = await counts(database)
        assert (await client.post("/mfa", data={**fields, "decision": "deny"})).status_code == 303
        assert await counts(database) == before_mfa
        response = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": "invalid",
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        assert response.status_code == 401 and await counts(database) == before_mfa
    values = await records(database)
    assert sorted(v["status"]["errorCode"] for v in values) == sorted(
        [50126, 50057, 65004, 50074, 7000215]
    )
    assert len({v["id"] for v in values}) == 5


async def test_unknown_and_internal_failures_not_fabricated(database, settings, monkeypatch):
    async with identity_client(database, settings) as (client, tenant, client_id, secret, *_):
        _, app = await provision(client, tenant)
        endpoint = f"/{tenant}/oauth2/v2.0/token"
        fields = await browser_start(client, tenant, app)
        assert (
            await client.post(
                "/login",
                data={
                    **fields,
                    "csrf": "wrong",
                    "username": "reader@contoso.test",
                    "password": "wrong",
                },
            )
        ).status_code == 400
        for body in [
            {"grant_type": "authorization_code", "code": "invalid", "client_id": app["client_id"]},
            {
                "grant_type": "refresh_token",
                "refresh_token": "invalid",
                "client_id": app["client_id"],
            },
            {
                "grant_type": "client_credentials",
                "client_id": str(uuid4()),
                "client_secret": "wrong",
                "scope": "https://graph.microsoft.com/.default",
            },
        ]:
            assert (await client.post(endpoint, data=body)).status_code in (400, 401)
        device = (
            await client.post(
                f"/{tenant}/oauth2/v2.0/devicecode",
                data={"client_id": app["client_id"], "scope": "User.Read"},
            )
        ).json()
        assert (
            await client.post(
                endpoint,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": app["client_id"],
                    "device_code": device["device_code"],
                },
            )
        ).status_code == 400
        from mockazure.oauth import routes

        async def signing_failure(*args, **kwargs):
            raise ValueError("private signing diagnostic")

        monkeypatch.setattr(routes, "issue_access_token", signing_failure)
        response = await client.post(
            endpoint,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": secret["secret"],
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        assert response.status_code == 503
        assert await records(database) == []
        from mockazure.evidence import authentication

        async def persistence_failure(*args, **kwargs):
            raise OperationalError("outage", None, Exception("private database diagnostic"))

        monkeypatch.setattr(authentication, "record_authentication_failure", persistence_failure)
        response = await client.post(
            "/login", data={**fields, "username": "reader@contoso.test", "password": "wrong"}
        )
        assert response.status_code == 503
        assert "private" not in response.text


async def test_legacy_device_outcomes_not_double_counted(database, settings):
    from mockazure.oauth.device_models import AuthenticationOutcome

    async with identity_client(database, settings) as (client, tenant, client_id, *_):
        user, _ = await provision(client, tenant)
        async with database.sessions.begin() as session:
            for flow in ("device_approval", "device_code"):
                session.add(
                    AuthenticationOutcome(
                        tenant_id=UUID(tenant),
                        client_id=UUID(client_id),
                        user_id=UUID(user["id"]),
                        flow=flow,
                        timestamp=datetime.now(UTC),
                        correlation_id=uuid4(),
                        result="success",
                        mfa="completed",
                    )
                )
    values = await records(database)
    assert len(values) == 1 and values[0]["userId"] == user["id"]
    assert values[0]["userPrincipalName"] is None and values[0]["appDisplayName"] is None
    assert values == await records(database)


async def test_common_authority_tenant_and_bounded_attempt_identity(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        _, app = await provision(client, tenant)
        fields = await browser_start(client, "common", app)
        correlation = str(uuid4())
        for _ in range(2):
            response = await client.post(
                "/login",
                headers={"client-request-id": correlation, "X-Forwarded-For": "203.0.113.99"},
                data={**fields, "username": "absent@contoso.test", "password": "wrong"},
            )
            assert response.status_code == 400
        response = await client.post(
            "/login", data={**fields, "username": "x" * 321, "password": "wrong"}
        )
        assert response.status_code == 400
    values = await records(database)
    assert len(values) == 2 and len({v["id"] for v in values}) == 2
    assert all(v["userId"] == "" and v["status"]["errorCode"] == 50126 for v in values)
    assert all(
        v["correlationId"] == correlation and v["ipAddress"] != "203.0.113.99" for v in values
    )
    async with database.sessions() as session:
        assert set(await session.scalars(select(EvidenceSource.tenant_id))) == {UUID(tenant)}


async def test_known_client_and_disabled_refresh_failures(database, settings):
    from datetime import timedelta

    from mockazure.identity.models import Application, Credential

    async with identity_client(database, settings) as (client, tenant, client_id, secret, *_):
        user, app = await provision(client, tenant)
        endpoint = f"/{tenant}/oauth2/v2.0/token"
        fields = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": secret["secret"],
            "scope": "https://graph.microsoft.com/.default",
        }
        async with database.sessions.begin() as session:
            credential = await session.scalar(
                select(Credential).where(
                    Credential.client_id == UUID(client_id), Credential.kind == "secret"
                )
            )
            credential.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert (await client.post(endpoint, data=fields)).status_code == 401
        async with database.sessions.begin() as session:
            (await session.get(Application, UUID(client_id))).enabled = False
        assert (await client.post(endpoint, data=fields)).status_code == 401
        async with database.sessions.begin() as session:
            (await session.get(Application, UUID(client_id))).enabled = True
        malformed = {k: v for k, v in fields.items() if k != "client_secret"}
        malformed.update(
            client_assertion="not-a-jwt",
            client_assertion_type="urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        )
        assert (await client.post(endpoint, data=malformed)).status_code == 401
        callback = await sign_in(client, tenant, app, scope="User.Read offline_access")
        tokens = (
            await client.post(
                endpoint,
                data={
                    "grant_type": "authorization_code",
                    "client_id": app["client_id"],
                    "redirect_uri": CALLBACK,
                    "code_verifier": VERIFIER,
                    "code": parse_qs(urlsplit(callback.headers["location"]).query)["code"][0],
                },
            )
        ).json()
        async with database.sessions.begin() as session:
            (await session.get(User, UUID(user["id"]))).enabled = False
        assert (
            await client.post(
                endpoint,
                data={
                    "grant_type": "refresh_token",
                    "client_id": app["client_id"],
                    "refresh_token": tokens["refresh_token"],
                },
            )
        ).status_code == 400
    values = await records(database)
    assert sorted(v["status"]["errorCode"] for v in values) == [0, 50057, 7000112, 7000222]
    failed_refresh = next(v for v in values if v["status"]["errorCode"] == 50057)
    assert failed_refresh["signInEventTypes"] == ["nonInteractiveUser"]

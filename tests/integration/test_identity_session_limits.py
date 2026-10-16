from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from sqlalchemy import delete, select

from mockazure.identity.models import Grant
from mockazure.oauth.device_models import AuthenticationOutcome
from mockazure.oauth.models import AuthorizationTransaction, LoginSession
from mockazure.oauth.sessions import cleanup_expired
from tests.integration.identity_support import identity_client
from tests.integration.test_identity_authorization import CALLBACK, VERIFIER, provision, sign_in


async def test_removed_grant_and_absolute_session_expiry(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        _, app = await provision(client, tenant)
        response = await sign_in(client, tenant, app, scope="User.Read offline_access")
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
        async with database.sessions() as session:
            login = await session.scalar(select(LoginSession))
            outcomes = list(await session.scalars(select(AuthenticationOutcome)))
            assert outcomes[0].flow == "authorization_code"
            expiry = login.expires_at
        client._transport.app.state.clock = lambda: expiry + timedelta(seconds=1)
        assert (await client.post(endpoint, data=fields)).json()["error"] == "invalid_grant"
        client._transport.app.state.clock = lambda: datetime.now(UTC)
        async with database.sessions.begin() as session:
            await session.execute(
                delete(Grant).where(
                    Grant.tenant_id == UUID(tenant), Grant.client_id == UUID(app["client_id"])
                )
            )
        assert (await client.post(endpoint, data=fields)).json()["error"] == "invalid_grant"


async def test_cleanup_preserves_live_codes_after_browser_transaction_expiry(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        _, app = await provision(client, tenant)
        response = await sign_in(client, tenant, app)
        code = parse_qs(urlsplit(response.headers["location"]).query)["code"][0]
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            row = await session.scalar(select(AuthorizationTransaction))
            row.expires_at = now - timedelta(seconds=1)
        async with database.sessions.begin() as session:
            await cleanup_expired(session, UUID(tenant), now)
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
        assert result.status_code == 200, result.text

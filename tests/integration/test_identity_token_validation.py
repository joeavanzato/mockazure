from datetime import UTC, datetime

import jwt
import pytest
from identity_support import identity_client

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "mutation",
    [
        {"tid": 42},
        {"oid": 42},
        {"azp": 42},
        {"sid": 42},
        {"iss": "https://other.test/issuer"},
        {"exp": True},
        {"aud": "https://other.test"},
    ],
)
async def test_malformed_token_claims_fail_as_unauthorized(database, settings, mutation):
    from mockazure.identity.keys import SigningKeyStore, read_wrapping_key
    from mockazure.identity.tokens import validate_access_token
    from mockazure.oauth.errors import OAuthError

    async with identity_client(database, settings) as (client, tenant, app, secret, _, _):
        response = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": app,
                "client_secret": secret["secret"],
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        claims = jwt.decode(response.json()["access_token"], options={"verify_signature": False})
        claims.update(mutation)
        async with database.sessions() as session:
            store = SigningKeyStore(
                session, read_wrapping_key(settings.data_dir), settings.endpoints
            )
            key = await store.active_key(datetime.now(UTC))
            raw = jwt.encode(
                claims,
                store.private_key(key),
                algorithm="RS256",
                headers={"kid": key.kid, "typ": "at+jwt"},
            )
            with pytest.raises(OAuthError) as error:
                await validate_access_token(
                    raw, "graph", datetime.now(UTC), session, endpoints=settings.endpoints
                )
            assert error.value.status == 401


async def test_id_tokens_and_forged_signatures_are_not_resource_tokens(database, settings):
    from mockazure.identity.keys import SigningKeyStore, read_wrapping_key
    from mockazure.identity.tokens import validate_access_token
    from mockazure.oauth.errors import OAuthError

    async with identity_client(database, settings) as (client, tenant, app, secret, _, _):
        response = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": app,
                "client_secret": secret["secret"],
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        raw = response.json()["access_token"]
        claims = jwt.decode(raw, options={"verify_signature": False})
        async with database.sessions() as session:
            store = SigningKeyStore(
                session, read_wrapping_key(settings.data_dir), settings.endpoints
            )
            key = await store.active_key(datetime.now(UTC))
            assert "PRIVATE KEY" not in key.encrypted_private
            id_token = jwt.encode(
                claims,
                store.private_key(key),
                algorithm="RS256",
                headers={"kid": key.kid, "typ": "JWT"},
            )
            for value in (id_token, raw.rsplit(".", 1)[0] + ".Zm9yZ2Vk"):
                with pytest.raises(OAuthError) as error:
                    await validate_access_token(
                        value, "graph", datetime.now(UTC), session, endpoints=settings.endpoints
                    )
                assert error.value.status == 401

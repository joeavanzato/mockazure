import asyncio
import threading
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from mockazure.identity.key_models import SigningKey
from mockazure.identity.keys import CLOCK_SKEW, TOKEN_SECONDS, SigningKeyStore, read_wrapping_key
from mockazure.identity.models import Credential
from mockazure.identity.tokens import issue_access_token, validate_access_token
from mockazure.identity.types import PrincipalContext
from tests.integration.identity_support import identity_client
from tests.integration.test_identity_authorization import CALLBACK, VERIFIER, provision, sign_in


async def test_common_authority_metadata_preserves_issuer_placeholder(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        user, app = await provision(client, tenant)
        for authority in ("common", "organizations"):
            response = await client.get(f"/{authority}/v2.0/.well-known/openid-configuration")
            assert response.status_code == 200, response.text
            metadata = response.json()
            assert metadata["issuer"] == settings.endpoints.login + "/{tenantid}/v2.0"
            assert metadata["token_endpoint"] == (
                settings.endpoints.login + f"/{authority}/oauth2/v2.0/token"
            )
            callback = await sign_in(client, authority, app)
            code = parse_qs(urlsplit(callback.headers["location"]).query)["code"][0]
            response = await client.post(
                metadata["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "client_id": app["client_id"],
                    "redirect_uri": CALLBACK,
                    "code": code,
                    "code_verifier": VERIFIER,
                },
            )
            assert response.status_code == 200, response.text
            me = await client.get(
                "/v1.0/me", headers={"Authorization": "Bearer " + response.json()["access_token"]}
            )
            assert me.json()["id"] == user["id"]


async def test_certificate_storage_discards_private_material(database, settings):
    async with identity_client(database, settings) as (client, tenant, app, _, key, cert):
        public = cert.public_bytes(serialization.Encoding.PEM).decode()
        private = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        response = await client.post(
            f"/_mock/tenants/{tenant}/identity/applications/{app}/credentials",
            json={"kind": "certificate", "public_certificate": public + private},
        )
        assert response.status_code == 201, response.text
        async with database.sessions() as session:
            row = await session.get(Credential, UUID(response.json()["id"]))
            assert row.public_certificate == public
            assert "PRIVATE KEY" not in row.public_certificate


async def test_rotation_uses_retirement_time_after_wait(database, settings, monkeypatch):
    import mockazure.identity.keys as keys_module

    async with identity_client(database, settings) as (_, tenant, app, *_):
        now = datetime.now(UTC)
        issued = now + timedelta(seconds=30)

        class Clock:
            @staticmethod
            def now(tz):
                return issued + timedelta(seconds=1)

        monkeypatch.setattr(keys_module, "datetime", Clock)
        wrapping = read_wrapping_key(settings.data_dir)
        context = PrincipalContext(
            UUID(tenant),
            UUID(app),
            UUID(app),
            "application",
            "00000003-0000-0000-c000-000000000000",
            frozenset({"Organization.Read.All"}),
        )
        async with database.sessions.begin() as session:
            token = await issue_access_token(
                context, issued, SigningKeyStore(session, wrapping, settings.endpoints)
            )
        async with database.sessions.begin() as session:
            # A caller queued this rotation before the later issuance.
            await SigningKeyStore(session, wrapping, settings.endpoints).rotate(now)
        boundary = issued + timedelta(seconds=TOKEN_SECONDS + CLOCK_SKEW - 1)
        async with database.sessions() as session:
            await validate_access_token(
                token, "graph", boundary, session, endpoints=settings.endpoints
            )
            jwks = await SigningKeyStore(session, wrapping, settings.endpoints).public_jwks(
                boundary
            )
            assert jwt.get_unverified_header(token)["kid"] in {key["kid"] for key in jwks["keys"]}


async def test_issuance_waits_for_inflight_rotation(database, settings, monkeypatch):
    import mockazure.identity.keys as keys_module

    async with identity_client(database, settings) as (_, tenant, app, *_):
        wrapping = read_wrapping_key(settings.data_dir)
        entered, release = threading.Event(), threading.Event()
        generate = keys_module.rsa.generate_private_key

        def paused_generate(**kwargs):
            entered.set()
            assert release.wait(timeout=10), "rotation test did not release key generation"
            return generate(**kwargs)

        monkeypatch.setattr(keys_module.rsa, "generate_private_key", paused_generate)

        async def rotate():
            async with database.sessions.begin() as session:
                row = await SigningKeyStore(session, wrapping, settings.endpoints).rotate(
                    datetime.now(UTC)
                )
                return row.kid

        async def issue():
            async with database.sessions.begin() as session:
                return await issue_access_token(
                    PrincipalContext(
                        UUID(tenant),
                        UUID(app),
                        UUID(app),
                        "application",
                        "00000003-0000-0000-c000-000000000000",
                        frozenset(),
                    ),
                    datetime.now(UTC),
                    SigningKeyStore(session, wrapping, settings.endpoints),
                )

        rotation = asyncio.create_task(rotate())
        assert await asyncio.to_thread(entered.wait, 5)
        issuance = asyncio.create_task(issue())
        try:
            # While rotation owns the transaction lock, issuance cannot use the retiring key.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(issuance), timeout=0.2)
        finally:
            release.set()
            new_kid, token = await asyncio.gather(rotation, issuance)
        assert jwt.get_unverified_header(token)["kid"] == new_kid
        async with database.sessions() as session:
            assert (await session.get(SigningKey, new_kid)).retired_at is None

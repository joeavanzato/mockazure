import asyncio
import base64
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import hashes
from identity_support import certificate, identity_client

pytestmark = pytest.mark.asyncio


def assertion(key, cert, tenant, client_id, **changes):
    now = int(datetime.now(UTC).timestamp())
    claims = {
        "iss": client_id,
        "sub": client_id,
        "aud": f"https://localhost:8443/{tenant}/oauth2/v2.0/token",
        "iat": now,
        "nbf": now,
        "exp": now + 600,
        "jti": str(uuid4()),
    }
    claims.update(changes)
    return jwt.encode(
        claims,
        key,
        algorithm="RS256",
        headers={
            "x5t": base64.urlsafe_b64encode(cert.fingerprint(hashes.SHA1())).decode().rstrip("=")
        },
    )


async def test_discovery_secret_tokens_and_rotation(database, settings):
    from mockazure.identity.keys import SigningKeyStore, read_wrapping_key
    from mockazure.identity.tokens import validate_access_token
    from mockazure.oauth.errors import OAuthError

    async with identity_client(database, settings) as (client, tenant, client_id, secret, _, _):
        response = await client.get(
            f"/{tenant}/v2.0/.well-known/openid-configuration",
            headers={"X-Forwarded-Host": "evil.test"},
        )
        assert response.status_code == 200
        metadata = response.json()
        assert metadata["issuer"] == f"https://localhost:8443/{tenant}/v2.0"
        assert metadata["token_endpoint"] == f"https://localhost:8443/{tenant}/oauth2/v2.0/token"
        request = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": secret["secret"],
            "scope": "https://graph.microsoft.com/.default",
        }
        result = await client.post(metadata["token_endpoint"], data=request)
        assert result.status_code == 200, result.text
        assert "refresh_token" not in result.json()
        token = result.json()["access_token"]
        async with database.sessions.begin() as session:
            context = await validate_access_token(
                token, "graph", datetime.now(UTC), session, endpoints=settings.endpoints
            )
            assert context.tenant_id == UUID(tenant)
            assert context.permissions == frozenset({"Organization.Read.All"})
            with pytest.raises(OAuthError) as wrong:
                await validate_access_token(
                    token, "exchange", datetime.now(UTC), session, endpoints=settings.endpoints
                )
            assert wrong.value.status == 401
            keys = SigningKeyStore(
                session, read_wrapping_key(settings.data_dir), settings.endpoints
            )
            await keys.rotate(datetime.now(UTC))
        jwks = (await client.get(metadata["jwks_uri"])).json()
        assert len(jwks["keys"]) == 2
        assert all("d" not in key and "p" not in key for key in jwks["keys"])
        async with database.sessions() as session:
            assert (
                await validate_access_token(
                    token, "graph", datetime.now(UTC), session, endpoints=settings.endpoints
                )
            ).client_id == UUID(client_id)
            with pytest.raises(OAuthError):
                await validate_access_token(
                    token,
                    "graph",
                    datetime.now(UTC) + timedelta(hours=2),
                    session,
                    endpoints=settings.endpoints,
                )
        wrong = await client.post(
            metadata["token_endpoint"], data={**request, "client_secret": "wrong"}
        )
        assert wrong.status_code == 401 and wrong.json()["error"] == "invalid_client"
        await client.delete(f"/_mock/tenants/{tenant}/identity/credentials/{secret['id']}")
        assert (await client.post(metadata["token_endpoint"], data=request)).status_code == 401


@pytest.mark.parametrize(
    "variant",
    [
        "valid",
        "ps256",
        "bad-signature",
        "unknown-key",
        "issuer",
        "subject",
        "audience",
        "missing-jti",
        "future",
        "expired",
        "excessive",
        "none",
        "hs256",
    ],
)
async def test_certificate_assertion_validation(database, settings, variant):
    async with identity_client(database, settings) as (client, tenant, client_id, _, key, cert):
        changes = {}
        if variant in ("issuer", "subject"):
            changes["iss" if variant == "issuer" else "sub"] = str(uuid4())
        if variant == "audience":
            changes["aud"] = "https://attacker.test/token"
        if variant == "missing-jti":
            changes["jti"] = None
        now = int(datetime.now(UTC).timestamp())
        if variant == "future":
            changes["nbf"] = now + 300
        if variant == "expired":
            changes["exp"] = now - 300
        if variant == "excessive":
            changes["exp"] = now + 7200
        signing_key, signing_cert = key, cert
        if variant in ("bad-signature", "unknown-key"):
            signing_key, other_cert = certificate()
            if variant == "unknown-key":
                signing_cert = other_cert
        raw = assertion(signing_key, signing_cert, tenant, client_id, **changes)
        claims = jwt.decode(raw, options={"verify_signature": False})
        if variant == "none":
            raw = jwt.encode(claims, "", algorithm="none")
        if variant == "hs256":
            raw = jwt.encode(claims, "a" * 32, algorithm="HS256")
        if variant == "ps256":
            raw = jwt.encode(
                claims,
                key,
                algorithm="PS256",
                headers={
                    "x5t#S256": base64.urlsafe_b64encode(cert.fingerprint(hashes.SHA256()))
                    .decode()
                    .rstrip("=")
                },
            )
        response = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": raw,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        assert response.status_code == (200 if variant in ("valid", "ps256") else 401), (
            response.text
        )


async def test_assertion_replay_is_atomic_and_forms_are_bounded(database, settings):
    async with identity_client(database, settings) as (
        client,
        tenant,
        client_id,
        secret,
        key,
        cert,
    ):
        url = f"/{tenant}/oauth2/v2.0/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": assertion(key, cert, tenant, client_id),
            "scope": "https://graph.microsoft.com/.default",
        }
        responses = await asyncio.gather(client.post(url, data=data), client.post(url, data=data))
        assert sorted(r.status_code for r in responses) == [200, 401]
        duplicate = await client.post(
            url,
            content="grant_type=client_credentials&client_id=a&client_id=b",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert duplicate.status_code == 400 and duplicate.json()["error"] == "invalid_request"
        oversized = await client.post(
            url,
            content="client_secret=" + "a" * 65536,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert oversized.status_code == 400
        mixed = await client.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": secret["secret"],
                "scope": "https://graph.microsoft.com/.default https://outlook.office365.com/.default",
            },
        )
        assert mixed.status_code == 400 and mixed.json()["error"] == "invalid_scope"


async def test_missing_wrapping_key_cannot_be_silently_replaced(database, settings):
    from mockazure.identity.keys import initialize_identity

    await initialize_identity(settings, database)
    path = settings.data_dir / "identity.wrap"
    original = path.read_bytes()
    path.unlink()
    with pytest.raises(ValueError, match="wrapping"):
        await initialize_identity(settings, database)
    assert not path.exists()
    path.write_bytes(original)
    await initialize_identity(settings, database)
    assert path.read_bytes() == original

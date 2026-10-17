import asyncio
import base64
import hmac
from datetime import UTC, datetime
from uuid import UUID

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mockazure.identity.credentials import verify_secret
from mockazure.identity.key_models import AssertionReplay
from mockazure.identity.models import Application, Credential, ServicePrincipal
from mockazure.oauth.errors import OAuthError


def denied():
    return OAuthError("invalid_client", "Client authentication failed", 401)


async def authenticate_client(session, tenant_id, fields, token_endpoint, now, *, request=None):
    try:
        client_id = UUID(fields.get("client_id", ""))
    except (ValueError, TypeError):
        raise denied() from None
    app = await session.get(Application, client_id)
    sp = await session.scalar(
        select(ServicePrincipal).where(
            ServicePrincipal.tenant_id == tenant_id, ServicePrincipal.client_id == client_id
        )
    )
    if not app or not sp or app.client_mode != "confidential":
        raise denied()

    async def classified(code):
        error = denied()
        if request is not None:
            from mockazure.evidence.authentication import annotate_failure

            error = await annotate_failure(
                error,
                session,
                request,
                tenant_id,
                client_id,
                None,
                "client_credentials",
                now,
                error_code=code,
                resource=getattr(request.state, "auth_resource", None),
            )
        return error

    if not app.enabled or not sp.enabled:
        raise await classified(7000112)
    secret, assertion = fields.get("client_secret"), fields.get("client_assertion")
    if bool(secret) == bool(assertion):
        raise denied()
    credentials = list(
        await session.scalars(
            select(Credential).where(
                Credential.client_id == client_id,
                Credential.tenant_id == app.tenant_id,
                Credential.revoked_at.is_(None),
                Credential.expires_at > now,
                Credential.starts_at <= now,
            )
        )
    )
    if secret:
        for credential in credentials:
            if credential.kind == "secret" and await asyncio.to_thread(
                verify_secret, secret, credential.verifier
            ):
                return app, sp
        inactive = list(
            await session.scalars(
                select(Credential).where(
                    Credential.client_id == client_id,
                    Credential.tenant_id == app.tenant_id,
                    Credential.kind == "secret",
                )
            )
        )
        for credential in inactive:
            if await asyncio.to_thread(verify_secret, secret, credential.verifier):
                if credential.revoked_at is None and credential.expires_at <= now:
                    raise await classified(7000222)
                raise denied()
        raise await classified(7000215)
    if fields.get("client_assertion_type") != (
        "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
    ):
        raise denied()
    try:
        header = jwt.get_unverified_header(assertion)
        if header.get("alg") not in ("RS256", "PS256"):
            raise ValueError()
        matched = None
        for credential in credentials:
            if credential.kind != "certificate":
                continue
            certificate = x509.load_pem_x509_certificate(credential.public_certificate.encode())
            if not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc:
                continue
            expected = {
                "x5t": base64.urlsafe_b64encode(certificate.fingerprint(hashes.SHA1()))
                .decode()
                .rstrip("="),
                "x5t#S256": base64.urlsafe_b64encode(certificate.fingerprint(hashes.SHA256()))
                .decode()
                .rstrip("="),
            }
            present = [name for name in expected if name in header]
            if present and all(
                isinstance(header[name], str) and hmac.compare_digest(header[name], expected[name])
                for name in present
            ):
                matched = certificate.public_key()
                break
        if matched is None:
            raise await classified(700027)
        claims = jwt.decode(
            assertion,
            matched,
            algorithms=["RS256", "PS256"],
            issuer=str(client_id),
            audience=token_endpoint,
            options={
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
                "require": ["iss", "sub", "aud", "exp", "nbf", "jti"],
            },
        )
        if claims["sub"] != str(client_id) or claims["aud"] != token_endpoint:
            raise ValueError()
        if not isinstance(claims["jti"], str) or not 1 <= len(claims["jti"]) <= 200:
            raise ValueError()
        if type(claims["exp"]) is not int or type(claims["nbf"]) is not int:
            raise ValueError()
        if claims["exp"] <= now.timestamp() - 60 or claims["nbf"] > now.timestamp() + 60:
            raise ValueError()
        if not 0 < claims["exp"] - claims["nbf"] <= 600:
            raise ValueError()
        if "iat" in claims and (
            type(claims["iat"]) is not int or claims["iat"] > now.timestamp() + 60
        ):
            raise ValueError()
    except (jwt.InvalidSignatureError, jwt.InvalidKeyError):
        raise await classified(700027) from None
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise denied() from None
    try:
        async with session.begin_nested():
            session.add(
                AssertionReplay(
                    tenant_id=tenant_id,
                    client_id=client_id,
                    jti=claims["jti"],
                    expires_at=datetime.fromtimestamp(claims["exp"], UTC),
                )
            )
            await session.flush()
    except IntegrityError as error:
        if getattr(error.orig, "sqlstate", None) == "23505":
            raise denied() from None
        raise
    return app, sp

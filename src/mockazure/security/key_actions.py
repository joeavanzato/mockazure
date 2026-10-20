"""Graph certificate actions: permission checks and proof checks are independent."""

from datetime import datetime
from uuid import UUID

import jwt
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from mockazure.identity.models import Credential
from mockazure.providers.graph.errors import GraphError
from mockazure.security.credentials import invalid, owner_condition, replace_certificates

PROOF_AUDIENCE = "00000002-0000-0000-c000-000000000000"


def verify_key_proof(
    proof: str, *, existing_keys: list, owner_id: UUID, audience: str, wall_now: datetime
) -> None:
    def denied():
        raise GraphError(400, "Authentication_MissingOrMalformed", "Key proof is invalid")

    if not isinstance(proof, str) or len(proof) > 16384:
        denied()
    try:
        parts = proof.split(".")
        if len(parts) != 3 or any("=" in part for part in parts):
            denied()
        header = jwt.get_unverified_header(proof)
        if header.get("alg") != "RS256" or header.get("crit"):
            denied()
        claims = jwt.decode(proof, options={"verify_signature": False})
        start, end = claims.get("nbf"), claims.get("exp")
        now = wall_now.timestamp()
        if (
            claims.get("iss") != str(owner_id)
            or claims.get("aud") != audience
            or type(start) is not int
            or type(end) is not int
            or not start <= now < end
            or not 0 < end - start <= 600
        ):
            denied()
    except (jwt.PyJWTError, ValueError, TypeError):
        denied()
    for row in existing_keys:
        if (
            row.kind != "certificate"
            or row.revoked_at is not None
            or row.owner_id not in {None, owner_id}
            or not row.starts_at <= wall_now < row.expires_at
        ):
            continue
        try:
            cert = x509.load_pem_x509_certificate(row.public_certificate.encode("ascii"))
            key = cert.public_key()
            if (
                not isinstance(key, rsa.RSAPublicKey)
                or key.key_size < 2048
                or not cert.not_valid_before_utc <= wall_now < cert.not_valid_after_utc
            ):
                continue
            jwt.decode(
                proof,
                key,
                algorithms=["RS256"],
                audience=audience,
                issuer=str(owner_id),
                options={
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                    "require": ["iss", "aud", "nbf", "exp"],
                },
            )
            return
        except (jwt.PyJWTError, ValueError, TypeError, AttributeError):
            continue
    denied()


async def key_action(session, actor, owner, body, wall_now, *, application, remove=False):
    allowed = {"keyId", "proof"} if remove else {"keyCredential", "passwordCredential", "proof"}
    required = {"keyId", "proof"} if remove else {"keyCredential", "proof"}
    if not isinstance(body, dict) or set(body) - allowed or not required <= set(body):
        invalid()
    owner_id = owner.object_id if application else owner.id
    existing = list(
        await session.scalars(
            select(Credential)
            .where(
                Credential.tenant_id == actor.tenant_id,
                Credential.client_id == owner.client_id,
                Credential.kind == "certificate",
                Credential.revoked_at.is_(None),
                owner_condition(owner_id, application),
            )
            .with_for_update()
        )
    )
    verify_key_proof(
        body["proof"],
        existing_keys=existing,
        owner_id=owner_id,
        audience=PROOF_AUDIENCE,
        wall_now=wall_now,
    )
    if remove:
        try:
            identifier = UUID(body["keyId"])
        except (ValueError, TypeError, AttributeError):
            invalid()
        row = next((row for row in existing if row.id == identifier), None)
        if row is None:
            invalid("Key credential was not found")
        row.revoked_at = wall_now
        await session.flush()
        return None
    if body.get("passwordCredential") is not None:
        invalid("This profile supports public AsymmetricX509Cert/Verify certificates only")
    rows = await replace_certificates(
        session,
        actor.tenant_id,
        owner.client_id,
        owner_id,
        [body["keyCredential"]],
        wall_now,
        application=application,
        append=True,
    )
    row = rows[0]
    return {
        "keyId": str(row.id),
        "displayName": row.display_name,
        "startDateTime": row.starts_at.isoformat(),
        "endDateTime": row.expires_at.isoformat(),
        "type": "AsymmetricX509Cert",
        "usage": "Verify",
        "key": None,
    }

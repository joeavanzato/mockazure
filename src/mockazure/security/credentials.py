import asyncio
import base64
import secrets
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import or_, select

from mockazure.identity.credentials import hash_secret
from mockazure.identity.models import Credential
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.schema_reference import validate_payload


def invalid(message="Credential does not match the supported Graph contract"):
    raise GraphError(400, "Request_BadRequest", message)


def timestamp(value, default):
    if value is None:
        return default
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result
    except (ValueError, AttributeError):
        invalid("Credential timestamp requires an explicit offset")


def owner_condition(owner, application):
    return (
        or_(Credential.owner_id == owner, Credential.owner_id.is_(None))
        if application
        else (Credential.owner_id == owner)
    )


async def add_password(session, tenant, client_id, owner, body, now):
    if not isinstance(body, dict) or set(body) - {"passwordCredential"}:
        invalid()
    value = body.get("passwordCredential") or {}
    if not isinstance(value, dict) or set(value) - {"displayName", "startDateTime", "endDateTime"}:
        invalid()
    try:
        validate_payload("v1.0", "passwordCredential", value, partial=True)
    except ValueError:
        invalid()
    start = timestamp(value.get("startDateTime"), now)
    end = timestamp(value.get("endDateTime"), start + timedelta(days=730))
    if end <= start or end <= now or len(value.get("displayName") or "") > 200:
        invalid("Credential end must follow start and current time")
    secret = secrets.token_urlsafe(32)
    row = Credential(
        tenant_id=tenant,
        client_id=client_id,
        owner_id=owner,
        kind="secret",
        verifier=await asyncio.to_thread(hash_secret, secret),
        starts_at=start,
        expires_at=end,
        display_name=value.get("displayName"),
    )
    session.add(row)
    await session.flush()
    return {
        "keyId": str(row.id),
        "displayName": row.display_name,
        "startDateTime": start.isoformat(),
        "endDateTime": end.isoformat(),
        "secretText": secret,
        "hint": secret[:3],
    }


async def remove_password(session, tenant, client_id, owner, body, now, *, application):
    if not isinstance(body, dict) or set(body) != {"keyId"}:
        invalid()
    try:
        identifier = UUID(body["keyId"])
    except (ValueError, TypeError, AttributeError):
        invalid()
    row = await session.scalar(
        select(Credential).where(
            Credential.tenant_id == tenant,
            Credential.client_id == client_id,
            Credential.id == identifier,
            Credential.kind == "secret",
            Credential.revoked_at.is_(None),
            owner_condition(owner, application),
        )
    )
    if row is None:
        invalid("Password credential was not found")
    row.revoked_at = now
    await session.flush()


async def replace_certificates(
    session, tenant, client_id, owner, values, now, *, application, append=False
):
    if not isinstance(values, list) or len(values) > 100:
        invalid()
    existing = list(
        await session.scalars(
            select(Credential).where(
                Credential.tenant_id == tenant,
                Credential.client_id == client_id,
                Credential.kind == "certificate",
                Credential.revoked_at.is_(None),
                owner_condition(owner, application),
            )
        )
    )
    if append and len(existing) + len(values) > 100:
        invalid("Certificate count exceeds the supported bound")
    prepared = []
    identifiers = set()
    for value in values:
        if not isinstance(value, dict) or set(value) - {
            "keyId",
            "type",
            "usage",
            "displayName",
            "key",
            "startDateTime",
            "endDateTime",
            "customKeyIdentifier",
        }:
            invalid()
        try:
            validate_payload("v1.0", "keyCredential", value, partial=True)
            identifier = UUID(value["keyId"]) if value.get("keyId") else uuid4()
            if (
                identifier in identifiers
                or value.get("type") != "AsymmetricX509Cert"
                or value.get("usage") != "Verify"
            ):
                raise ValueError()
            identifiers.add(identifier)
            cert = x509.load_der_x509_certificate(base64.b64decode(value["key"], validate=True))
            key = cert.public_key()
            if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
                raise ValueError()
            start = max(
                timestamp(value.get("startDateTime"), cert.not_valid_before_utc),
                cert.not_valid_before_utc,
            )
            end = min(
                timestamp(value.get("endDateTime"), cert.not_valid_after_utc),
                cert.not_valid_after_utc,
            )
            if end <= start or end <= now or len(value.get("displayName") or "") > 200:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            invalid("A public RSA certificate and valid lifetime are required")
        row = next((x for x in existing if x.id == identifier), None)
        if append and row is not None:
            invalid("Credential identifier is already in use")
        if row is None:
            if await session.get(Credential, identifier):
                invalid("Credential identifier is already in use")
            row = Credential(
                id=identifier,
                tenant_id=tenant,
                client_id=client_id,
                owner_id=owner,
                kind="certificate",
            )
        row.starts_at, row.expires_at = start, end
        row.public_certificate = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
        row.thumbprint = cert.fingerprint(hashes.SHA256()).hex()
        row.display_name = value.get("displayName")
        prepared.append(row)
    for row in existing:
        if not append and row.id not in identifiers:
            row.revoked_at = now
    session.add_all(prepared)
    await session.flush()
    return prepared

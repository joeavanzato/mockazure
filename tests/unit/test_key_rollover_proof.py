from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from mockazure.providers.graph.errors import GraphError
from mockazure.security.key_actions import verify_key_proof
from tests.integration.identity_support import certificate

AUDIENCE = "00000002-0000-0000-c000-000000000000"


def proof(key, owner, now, **changes):
    return jwt.encode(
        {
            "iss": str(owner),
            "aud": AUDIENCE,
            "nbf": int(now.timestamp()),
            "exp": int(now.timestamp()) + 600,
            **changes,
        },
        key,
        algorithm="RS256",
    )


def eligible(cert, owner, now, **changes):
    return SimpleNamespace(
        **(
            {
                "owner_id": owner,
                "revoked_at": None,
                "kind": "certificate",
                "starts_at": now - timedelta(minutes=1),
                "expires_at": now + timedelta(hours=1),
                "public_certificate": cert.public_bytes(serialization.Encoding.PEM).decode(),
            }
            | changes
        )
    )


def test_proof_requires_existing_eligible_key():
    now, owner = datetime.now(UTC), uuid4()
    key, cert = certificate()
    value = proof(key, owner, now)
    verify_key_proof(
        value,
        existing_keys=[eligible(cert, owner, now)],
        owner_id=owner,
        audience=AUDIENCE,
        wall_now=now,
    )
    for rows in (
        [],
        [eligible(cert, uuid4(), now)],
        [eligible(cert, owner, now, revoked_at=now)],
        [eligible(cert, owner, now, expires_at=now)],
        [eligible(cert, owner, now, starts_at=now + timedelta(seconds=1))],
    ):
        with pytest.raises(GraphError):
            verify_key_proof(
                value, existing_keys=rows, owner_id=owner, audience=AUDIENCE, wall_now=now
            )


@pytest.mark.parametrize(
    "change",
    [
        {"iss": str(uuid4())},
        {"aud": "wrong"},
        {"nbf": 0, "exp": 600},
        {"exp": 9999999999},
        {"nbf": "invalid"},
        {"exp": True},
    ],
)
def test_wrong_owner_audience_signature_and_time_fail(change):
    now, owner = datetime.now(UTC), uuid4()
    key, cert = certificate()
    with pytest.raises(GraphError):
        verify_key_proof(
            proof(key, owner, now, **change),
            existing_keys=[eligible(cert, owner, now)],
            owner_id=owner,
            audience=AUDIENCE,
            wall_now=now,
        )


def test_new_key_cannot_authorize_its_own_addition():
    now, owner = datetime.now(UTC), uuid4()
    _, cert = certificate()
    new_key, _ = certificate()
    with pytest.raises(GraphError):
        verify_key_proof(
            proof(new_key, owner, now),
            existing_keys=[eligible(cert, owner, now)],
            owner_id=owner,
            audience=AUDIENCE,
            wall_now=now,
        )

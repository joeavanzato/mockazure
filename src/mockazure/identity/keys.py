"""Persistent instance identity. Startup never creates or replaces wrapping material."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import jwt
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import or_, select, text

from mockazure.endpoints import EndpointProfile
from mockazure.files import file_lock, write_private
from mockazure.identity.key_models import SigningKey

KEY_LOCK = 726385210011
TOKEN_SECONDS = 3600
CLOCK_SKEW = 60


def read_wrapping_key(directory: Path) -> bytes:
    try:
        value = (directory / "identity.wrap").read_bytes().strip()
        Fernet(value)
        return value
    except (ValueError, OSError):
        raise ValueError(
            "Identity wrapping key is missing or invalid; restore instance backup"
        ) from None


class SigningKeyStore:
    def __init__(self, session, wrapping_key: bytes, endpoints: EndpointProfile):
        self.session, self.cipher, self.endpoints = session, Fernet(wrapping_key), endpoints

    def private_key(self, row: SigningKey):
        try:
            return serialization.load_pem_private_key(
                self.cipher.decrypt(row.encrypted_private.encode("ascii")), password=None
            )
        except (InvalidToken, ValueError):
            raise ValueError(
                "Identity wrapping key does not match stored signing identity"
            ) from None

    async def active_key(self, now: datetime) -> SigningKey:
        row = await self.session.scalar(select(SigningKey).where(SigningKey.retired_at.is_(None)))
        if row is None:
            raise ValueError("Identity has not been initialized")
        return row

    async def public_jwks(self, now: datetime) -> dict:
        rows = await self.session.scalars(
            select(SigningKey).where(
                or_(
                    SigningKey.retired_at.is_(None),
                    SigningKey.retired_at > now - timedelta(seconds=TOKEN_SECONDS + CLOCK_SKEW),
                )
            )
        )
        return {"keys": [row.public_jwk for row in rows]}

    async def rotate(self, now: datetime) -> SigningKey:
        await self.session.execute(text(f"SELECT pg_advisory_xact_lock({KEY_LOCK})"))
        active = await self.session.scalar(
            select(SigningKey).where(SigningKey.retired_at.is_(None))
        )
        if active:
            self.private_key(active)  # Never replace unreadable identity material.
            active.retired_at = now
            await self.session.flush()
        key = await asyncio.to_thread(
            rsa.generate_private_key, public_exponent=65537, key_size=2048
        )
        kid = uuid4().hex
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        jwk.update(kid=kid, use="sig", alg="RS256")
        raw = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        row = SigningKey(
            kid=kid,
            public_jwk=jwk,
            encrypted_private=self.cipher.encrypt(raw).decode("ascii"),
            created_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return row


async def initialize_identity(settings, database) -> str:
    async with database.sessions.begin() as session:
        await session.execute(text(f"SELECT pg_advisory_xact_lock({KEY_LOCK})"))
        exists = await session.scalar(select(SigningKey.kid).limit(1))
        path = settings.data_dir / "identity.wrap"
        with file_lock(settings.data_dir, ".identity.lock"):
            if not path.exists():
                if exists:
                    raise ValueError("Identity wrapping key is missing; restore instance backup")
                write_private(path, Fernet.generate_key() + b"\n")
            wrapping = read_wrapping_key(settings.data_dir)
        store = SigningKeyStore(session, wrapping, settings.endpoints)
        if exists:
            row = await store.active_key(datetime.now(UTC))
            store.private_key(row)
        else:
            row = await store.rotate(datetime.now(UTC))
        return row.kid


async def identity_ready(settings, database) -> bool:
    async with database.sessions() as session:
        exists = await session.scalar(select(SigningKey.kid).limit(1))
        # Foundation-only instances are usable before explicit identity initialization.
        if not exists and not (settings.data_dir / "identity.wrap").exists():
            return True
        try:
            store = SigningKeyStore(
                session, read_wrapping_key(settings.data_dir), settings.endpoints
            )
            store.private_key(await store.active_key(datetime.now(UTC)))
            return True
        except ValueError:
            return False

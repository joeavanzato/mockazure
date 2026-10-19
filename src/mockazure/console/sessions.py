"""PostgreSQL sessions and shared throttling for all API replicas."""

import asyncio
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, text, update

from mockazure.admin.errors import AdminError
from mockazure.console.models import ConsoleCredential, ConsoleSession, LoginFailure
from mockazure.console.passwords import hash_password, verify_password

COOKIE = "mockazure_lab"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class IssuedSession:
    cookie: str
    csrf_token: str
    expires_at: datetime


async def set_password(database, password):
    encoded = await asyncio.to_thread(hash_password, password)
    async with database.sessions.begin() as session:
        await session.execute(text("SELECT pg_advisory_xact_lock(726385210077)"))
        row = await session.get(ConsoleCredential, 1, with_for_update=True)
        if row:
            row.encoded, row.generation = encoded, row.generation + 1
        else:
            session.add(ConsoleCredential(id=1, encoded=encoded, generation=1))
        await session.execute(update(ConsoleSession).values(revoked=True))
        await session.execute(delete(LoginFailure))


async def login(session, password, source, now):
    row = await session.get(ConsoleCredential, 1, with_for_update=True)
    if row is None:
        raise AdminError(
            401, "ConsoleLocked", "Set a lab password using mockazure console password set"
        )
    await session.execute(
        delete(LoginFailure).where(LoginFailure.occurred_at <= now - timedelta(minutes=1))
    )
    failures = list(
        await session.scalars(select(LoginFailure).order_by(LoginFailure.occurred_at).limit(31))
    )
    source_hash = digest(source)
    if len(failures) >= 30 or sum(f.source == source_hash for f in failures) >= 5:
        raise AdminError(429, "LoginThrottled", "Too many failed logins; retry in one minute")
    if not await asyncio.to_thread(verify_password, password, row.encoded):
        session.add(LoginFailure(source=source_hash, occurred_at=now))
        raise AdminError(401, "InvalidPassword", "The lab password was not accepted")
    await session.execute(delete(ConsoleSession).where(ConsoleSession.expires_at <= now))
    cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    expires = now + timedelta(hours=8)
    session.add(
        ConsoleSession(
            token_hash=digest(cookie),
            csrf_token=csrf,
            generation=row.generation,
            expires_at=expires,
        )
    )
    return IssuedSession(cookie, csrf, expires)


async def authenticate(session, cookie, now):
    if not cookie or len(cookie) != 43:
        return None
    return await session.scalar(
        select(ConsoleSession)
        .join(
            ConsoleCredential,
            (ConsoleCredential.id == 1)
            & (ConsoleCredential.generation == ConsoleSession.generation),
        )
        .where(
            ConsoleSession.token_hash == digest(cookie),
            ConsoleSession.revoked.is_(False),
            ConsoleSession.expires_at > now,
        )
    )

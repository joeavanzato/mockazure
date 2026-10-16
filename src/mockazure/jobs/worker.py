import asyncio
import signal
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from mockazure.db.engine import Database
from mockazure.jobs.repository import claim_job, fail_job, finish_export


async def run_one(database: Database, *, owner: UUID, now: datetime) -> bool:
    async with database.sessions.begin() as session:
        claim = await claim_job(session, owner=owner, now=now, lease_seconds=30)
    if claim is None:
        return False
    try:
        async with database.sessions.begin() as session:
            await finish_export(session, claim, claim.payload)
    except SQLAlchemyError:
        async with database.sessions.begin() as session:
            await fail_job(
                session, claim, error_code="TransientError", now=datetime.now(timezone.utc)
            )
    return True


async def serve_worker(database: Database):
    stopped = False

    def stop(*args):
        nonlocal stopped
        stopped = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    owner = uuid4()
    try:
        while not stopped:
            try:
                worked = await run_one(database, owner=owner, now=datetime.now(timezone.utc))
            except SQLAlchemyError:
                worked = False
            if not worked:
                await asyncio.sleep(0.5)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

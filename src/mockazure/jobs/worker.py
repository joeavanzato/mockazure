import asyncio
import signal
from datetime import datetime, timedelta, timezone
from time import monotonic
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from mockazure.db.engine import Database
from mockazure.evidence.sources import scan_sources
from mockazure.jobs.dispatch import dispatch_job
from mockazure.jobs.repository import claim_job


async def run_one(database: Database, *, owner: UUID, now: datetime, repair: bool = True) -> bool:
    started = monotonic()

    def current_time():
        return now + timedelta(seconds=monotonic() - started)

    if repair:
        async with database.sessions.begin() as session:
            await scan_sources(session)
    async with database.sessions.begin() as session:
        claim = await claim_job(session, owner=owner, now=current_time(), lease_seconds=30)
    if claim is None:
        return False
    await dispatch_job(database, claim, current_time())
    return True


async def run_cycle(database: Database, *, owner: UUID, now: datetime, repair: bool = True) -> bool:
    from mockazure.evidence.bulk import run_historical_batch

    started = monotonic()
    worked = await run_one(database, owner=owner, now=now, repair=repair)
    try:
        bulk = await run_historical_batch(
            database,
            owner=owner,
            now=now + timedelta(seconds=monotonic() - started),
        )
    except (SQLAlchemyError, ValueError, TypeError, KeyError):
        # Rollback the optional fast path. The ordinary dispatcher owns retries
        # and terminal classification; it is always serviced first next cycle.
        bulk = 0
    return worked or bool(bulk)


async def serve_worker(database: Database):
    stopped = False

    def stop(*args):
        nonlocal stopped
        stopped = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    owner = uuid4()
    next_repair = 0.0
    try:
        while not stopped:
            try:
                repair = monotonic() >= next_repair
                if repair:
                    next_repair = monotonic() + 5
                worked = await run_cycle(
                    database, owner=owner, now=datetime.now(timezone.utc), repair=repair
                )
            except SQLAlchemyError:
                worked = False
            if not worked:
                await asyncio.sleep(0.5)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

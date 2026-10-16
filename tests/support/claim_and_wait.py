"""Crash-recovery probe using the production claim transaction."""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from mockazure.asyncio_support import run
from mockazure.config import load_settings
from mockazure.db.engine import close_database, create_database
from mockazure.jobs.repository import claim_job


async def claim():
    database = create_database(load_settings(Path(sys.argv[1])))
    try:
        async with database.sessions.begin() as session:
            claimed = await claim_job(
                session, owner=uuid4(), now=datetime.now(timezone.utc), lease_seconds=1
            )
        Path(sys.argv[2]).write_text(str(claimed.id))
    finally:
        await close_database(database)


run(claim())
time.sleep(60)

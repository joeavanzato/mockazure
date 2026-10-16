import concurrent.futures
import multiprocessing

from mockazure.db.engine import close_database, create_database
from mockazure.db.migrations import migrate, schema_is_current


async def test_concurrent_migrations_serialize_and_recheck(settings):
    url = settings.database_url.get_secret_value()
    database = create_database(settings)
    assert not await schema_is_current(database)
    with concurrent.futures.ProcessPoolExecutor(
        2, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        futures = [pool.submit(migrate, url) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)
    assert await schema_is_current(database)
    migrate(url)
    assert await schema_is_current(database)
    await close_database(database)

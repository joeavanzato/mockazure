from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mockazure.config import Settings


@dataclass
class Database:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]


def create_database(settings: Settings) -> Database:
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.pool_size,
        max_overflow=settings.pool_max_overflow,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 3},
    )
    return Database(engine, async_sessionmaker(engine, expire_on_commit=False))


async def close_database(database: Database) -> None:
    await database.engine.dispose()

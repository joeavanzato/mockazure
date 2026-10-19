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
    settings: Settings
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
    return Database(
        engine,
        settings,
        async_sessionmaker(
            engine,
            expire_on_commit=False,
            info={
                "query_limits": {
                    "seconds": settings.query_timeout_seconds,
                    "rows": settings.query_max_rows,
                    "bytes": settings.query_max_bytes,
                    "expanded": settings.query_max_expanded_rows,
                },
                "diagnostic_delivery": {
                    "delay": settings.diagnostic_delay_seconds,
                    "retry": settings.diagnostic_retry_seconds,
                    "attempts": settings.diagnostic_max_attempts,
                },
                "notification_receivers": settings.notification_receivers,
                "graph_read_budget": {
                    "limit": settings.graph_collection_request_limit,
                    "window_seconds": settings.graph_collection_window_seconds,
                },
            },
        ),
    )


async def close_database(database: Database) -> None:
    await database.engine.dispose()

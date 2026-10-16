from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from mockazure.db.engine import Database


def configuration():
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
    return config


def migrate(database_url: str) -> None:
    engine = create_engine(database_url, hide_parameters=True, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            # Transaction lock is held by the exact connection Alembic uses.
            connection.execute(text("SELECT pg_advisory_xact_lock(726385210010)"))
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    except (SQLAlchemyError, CommandError):
        raise ValueError(
            "Database migration failed; check PostgreSQL availability and schema"
        ) from None
    finally:
        engine.dispose()


async def schema_is_current(database: Database) -> bool:
    try:
        async with database.engine.connect() as connection:
            exists = await connection.scalar(text("SELECT to_regclass('public.alembic_version')"))
            if exists is None:
                return False
            versions = (
                (await connection.execute(text("SELECT version_num FROM alembic_version")))
                .scalars()
                .all()
            )
            return versions == [ScriptDirectory.from_config(configuration()).get_current_head()]
    except SQLAlchemyError:
        return False

from uuid import uuid4

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from mockazure.db.migrations import configuration, migrate


def test_population_upgrade_preserves_existing_tenant(database_url):
    identifier = uuid4()
    engine = create_engine(database_url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "074")
            connection.execute(
                text(
                    "INSERT INTO tenants(id,slug,display_name,domain,licences,revision)"
                    " VALUES (:id,'prior','Prior','prior.test','[]',1)"
                ),
                {"id": identifier},
            )
        migrate(database_url)
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT display_name FROM tenants WHERE id=:id"), {"id": identifier}
                )
                == "Prior"
            )
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == ScriptDirectory.from_config(configuration()).get_current_head()
            )
            assert connection.scalar(text("SELECT count(*) FROM population_runs")) == 0
    finally:
        engine.dispose()

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from mockazure.config import Settings
from mockazure.db.engine import close_database, create_database
from mockazure.db.migrations import migrate


@pytest.fixture
def database_url():
    password = Path(".runtime/test-db.password").read_text().strip()
    name = "mockazure_test_" + uuid4().hex
    url = make_url("postgresql+psycopg://mockazure@127.0.0.1:55433/" + name).set(password=password)
    assert url.database.startswith("mockazure_test_")
    control = url.set(drivername="postgresql", database="mockazure_test_control").render_as_string(
        hide_password=False
    )
    with psycopg.connect(control, autocommit=True, connect_timeout=5) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        with psycopg.connect(control, autocommit=True, connect_timeout=5) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def settings(database_url, tmp_path):
    return Settings(data_dir=tmp_path, database_url=database_url)


@pytest.fixture
async def database(settings):
    migrate(settings.database_url.get_secret_value())
    database = create_database(settings)
    yield database
    await close_database(database)


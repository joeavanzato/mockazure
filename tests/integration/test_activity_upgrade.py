from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from mockazure.db.migrations import configuration
from mockazure.db.models import Tenant


def test_activity_upgrade_preserves_prior_data_and_refuses_lossy_downgrade(settings):
    engine = create_engine(settings.database_url.get_secret_value())
    tenant = uuid4()
    try:
        with engine.begin() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "048")
            connection.execute(
                Tenant.__table__.insert().values(
                    id=tenant,
                    slug="activity-upgrade",
                    display_name="Keep",
                    domain="activity-upgrade.test",
                    licences=[],
                    revision=9,
                )
            )
            command.upgrade(config, "head")
            assert (
                connection.scalar(text("SELECT revision FROM tenants WHERE id=:id"), {"id": tenant})
                == 9
            )
            connection.execute(
                text(
                    "INSERT INTO activity_states(tenant_id,generation,revision,settings) "
                    "VALUES(:id,:gen,0,jsonb_build_object('page_size',2))"
                ),
                {"id": tenant, "gen": uuid4()},
            )
        with pytest.raises(DBAPIError, match="Export and reset activity"):
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "048")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM activity_states"))
            config.attributes["connection"] = connection
            command.downgrade(config, "048")
            command.upgrade(config, "head")
            assert (
                connection.scalar(text("SELECT revision FROM tenants WHERE id=:id"), {"id": tenant})
                == 9
            )
    finally:
        engine.dispose()

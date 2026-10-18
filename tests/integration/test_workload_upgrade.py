from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from mockazure.db.migrations import configuration
from mockazure.db.models import Tenant


def test_workload_upgrade_preserves_tenant_and_refuses_lossy_downgrade(settings):
    engine = create_engine(settings.database_url.get_secret_value())
    tenant = uuid4()
    try:
        with engine.begin() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "043")
            connection.execute(
                Tenant.__table__.insert().values(
                    id=tenant,
                    slug="upgrade-sites",
                    display_name="Preserved",
                    domain="upgrade.test",
                    licences=[],
                    revision=7,
                )
            )
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            assert (
                connection.scalar(text("SELECT revision FROM tenants WHERE id=:id"), {"id": tenant})
                == 7
            )
            connection.execute(
                text(
                    "INSERT INTO workload_scopes("
                    "tenant_id,workload,scope,revision,generation,grant_revision) "
                    "VALUES(:id,'sharepoint','local.test,site,web',0,:generation,0)"
                ),
                {"id": tenant, "generation": uuid4()},
            )
        with pytest.raises(DBAPIError, match="Export and clear workload evidence"):
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "043")
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT count(*) FROM workload_scopes")) == 1
            connection.execute(text("DELETE FROM workload_scopes"))
            config.attributes["connection"] = connection
            command.downgrade(config, "043")
            command.upgrade(config, "head")
    finally:
        engine.dispose()

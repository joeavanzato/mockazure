from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from mockazure.db.migrations import configuration
from mockazure.db.models import Tenant


def test_method_management_upgrade_preserves_verifier_and_refuses_lossy_downgrade(settings):
    engine = create_engine(settings.database_url.get_secret_value())
    tenant = uuid4()
    try:
        with engine.begin() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "049")
            connection.execute(
                Tenant.__table__.insert().values(
                    id=tenant,
                    slug="method-upgrade",
                    display_name="Keep",
                    domain="method-upgrade.test",
                    licences=[],
                    revision=9,
                )
            )
            user = uuid4()
            connection.execute(
                text(
                    "INSERT INTO identity_users "
                    "(id,tenant_id,upn,display_name,verifier,enabled,profile) "
                    "VALUES(:user,:tenant,'upgrade@method.test','Keep','original-verifier',true,'{}')"
                ),
                {"user": user, "tenant": tenant},
            )
            command.upgrade(config, "head")
            assert (
                connection.scalar(text("SELECT revision FROM tenants WHERE id=:id"), {"id": tenant})
                == 9
            )
            assert (
                connection.scalar(
                    text("SELECT verifier FROM identity_users WHERE id=:id"), {"id": user}
                )
                == "original-verifier"
            )
            assert (
                connection.scalar(
                    text("SELECT credential_version FROM identity_users WHERE id=:id"), {"id": user}
                )
                == 0
            )
            connection.execute(
                text(
                    "UPDATE identity_users SET credential_version=1,password_change_required=true "
                    "WHERE id=:id"
                ),
                {"id": user},
            )
        with pytest.raises(DBAPIError, match="Cannot downgrade persisted password reset state"):
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "049")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE identity_users SET credential_version=0,password_change_required=false"
                )
            )
            config.attributes["connection"] = connection
            command.downgrade(config, "049")
            command.upgrade(config, "head")
            assert (
                connection.scalar(text("SELECT revision FROM tenants WHERE id=:id"), {"id": tenant})
                == 9
            )
    finally:
        engine.dispose()

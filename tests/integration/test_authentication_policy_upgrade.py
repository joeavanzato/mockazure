from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import MetaData, Table, create_engine, select, text
from sqlalchemy.exc import DBAPIError

from mockazure.db.migrations import configuration
from mockazure.db.models import Tenant


def test_policy_upgrade_preserves_old_tenant_and_refuses_lossy_downgrade(settings):
    engine = create_engine(settings.database_url.get_secret_value())
    tenant = uuid4()
    try:
        with engine.begin() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "051")
            connection.execute(
                Tenant.__table__.insert().values(
                    id=tenant,
                    slug="policy-upgrade",
                    display_name="Keep",
                    domain="policy-upgrade.test",
                    licences=[],
                    revision=17,
                )
            )
            historical = Table("security_objects", MetaData(), autoload_with=connection)
            policy_id = uuid4()
            policy = {
                "id": str(policy_id),
                "displayName": "Preserved policy",
                "state": "disabled",
                "conditions": {
                    "users": {"includeUsers": ["All"]},
                    "applications": {"includeApplications": ["All"]},
                    "clientAppTypes": ["all"],
                },
                "grantControls": {"operator": "OR", "builtInControls": ["mfa"]},
            }
            connection.execute(
                historical.insert().values(
                    tenant_id=tenant,
                    id=policy_id,
                    kind="conditionalAccessPolicy",
                    properties=policy,
                )
            )
            command.upgrade(config, "head")
            assert (
                connection.scalar(
                    select(historical.c.properties).where(historical.c.id == policy_id)
                )
                == policy
            )
            assert (
                connection.scalar(text("SELECT revision FROM tenants WHERE id=:id"), {"id": tenant})
                == 17
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT enforced FROM authentication_method_policy_state "
                        "WHERE tenant_id=:id"
                    ),
                    {"id": tenant},
                )
                is False
            )
            assert (
                connection.scalar(text("SELECT count(*) FROM trusted_authentication_contexts")) == 0
            )
            connection.execute(
                text(
                    "UPDATE authentication_method_policy_state SET enforced=true "
                    "WHERE tenant_id=:id"
                ),
                {"id": tenant},
            )
        with pytest.raises(
            DBAPIError, match="Cannot downgrade persisted authentication policy state"
        ):
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "051")
        with engine.begin() as connection:
            connection.execute(text("UPDATE authentication_method_policy_state SET enforced=false"))
            config.attributes["connection"] = connection
            command.downgrade(config, "051")
            command.upgrade(config, "head")
            assert (
                connection.scalar(
                    text(
                        "SELECT enforced FROM authentication_method_policy_state "
                        "WHERE tenant_id=:id"
                    ),
                    {"id": tenant},
                )
                is False
            )
    finally:
        engine.dispose()

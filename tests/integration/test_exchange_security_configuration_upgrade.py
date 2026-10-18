"""Configuration upgrade preserves existing mailbox state and refuses destructive downgrade."""

import pytest
from alembic import command as alembic_command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from mockazure.db.migrations import configuration
from tests.integration.graph_support import graph_client
from tests.integration.test_exchange_mail import command
from tests.integration.test_exchange_security_configuration import setup


async def test_configuration_upgrade_and_lossy_downgrade(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app, user)
        engine = create_engine(settings.database_url.get_secret_value())
        try:
            config = configuration()
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                alembic_command.downgrade(config, "054")
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM mail_mailboxes WHERE tenant_id=:tenant"),
                        {"tenant": tenant},
                    )
                    == 1
                )
                alembic_command.upgrade(config, "head")
            result = await command(
                client,
                tenant,
                headers,
                "New-RemoteDomain",
                {"Name": "Upgrade", "DomainName": "upgrade.example.test"},
            )
            assert result.status_code == 200, result.text
            with pytest.raises(DBAPIError, match="Cannot discard Exchange security configuration"):
                with engine.begin() as connection:
                    config.attributes["connection"] = connection
                    alembic_command.downgrade(config, "054")
        finally:
            engine.dispose()

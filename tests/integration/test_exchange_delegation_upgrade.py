"""Migration and API restart retain operational delegation and refuse silent loss."""

import httpx
import pytest
from alembic import command as alembic_command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from mockazure.admin.app import create_app
from mockazure.db.migrations import configuration
from tests.integration.graph_support import graph_client
from tests.integration.test_exchange_mail import command
from tests.integration.test_exchange_recipients import setup


async def test_delegation_upgrade_restart_and_lossy_downgrade(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        headers, delegate = await setup(client, database, settings, tenant, app, owner)
        engine = create_engine(settings.database_url.get_secret_value())
        try:
            config = configuration()
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                alembic_command.downgrade(config, "053")
                assert (
                    connection.scalar(
                        text("SELECT primary_smtp FROM mail_mailboxes WHERE user_id=:id"),
                        {"id": owner},
                    )
                    == "reader@contoso.test"
                )
                alembic_command.upgrade(config, "head")
            added = await command(
                client,
                tenant,
                headers,
                "Add-RecipientPermission",
                {"Identity": str(owner), "Trustee": str(delegate), "AccessRights": ["SendAs"]},
            )
            assert added.status_code == 200, added.text
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(settings, database)),
                base_url="https://localhost:8443",
            ) as restarted:
                actual = await command(
                    restarted, tenant, headers, "Get-RecipientPermission", {"Identity": str(owner)}
                )
                assert actual.status_code == 200, actual.text
                assert actual.json()["value"][0]["Trustee"] == "delegate@contoso.test"
            with pytest.raises(DBAPIError, match="Cannot discard operational mailbox delegation"):
                with engine.begin() as connection:
                    config.attributes["connection"] = connection
                    alembic_command.downgrade(config, "053")
        finally:
            engine.dispose()

from uuid import uuid4

from alembic import command
from sqlalchemy import create_engine, select

from mockazure.db.engine import close_database, create_database
from mockazure.db.migrations import configuration, migrate
from mockazure.db.models import Tenant
from tests.integration.identity_support import identity_client


async def test_explicit_m1_upgrade_preserves_tenant_and_supports_identity(settings):
    tenant_id = uuid4()
    engine = create_engine(settings.database_url.get_secret_value())
    with engine.begin() as connection:
        config = configuration()
        config.attributes["connection"] = connection
        command.upgrade(config, "002")
        connection.execute(
            Tenant.__table__.insert().values(
                id=tenant_id,
                slug="preserved",
                display_name="Preserved M1",
                domain="preserved.test",
                licences=[],
                revision=1,
            )
        )
    engine.dispose()
    migrate(settings.database_url.get_secret_value())
    database = create_database(settings)
    try:
        async with database.sessions() as session:
            assert (
                await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
            ).display_name == ("Preserved M1")
        async with identity_client(database, settings) as (client, tenant, client_id, secret, *_):
            token = await client.post(
                f"/{tenant}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": secret["secret"],
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
            assert token.status_code == 200
    finally:
        await close_database(database)

from uuid import uuid4

from alembic import command
from sqlalchemy import MetaData, Table, create_engine, select

from mockazure.db.migrations import configuration, migrate
from mockazure.db.models import Tenant


def test_upgrade_preserves_each_legacy_reader_identity_including_duplicates(settings):
    tenant, user, subscription, first, second = (uuid4() for _ in range(5))
    engine = create_engine(settings.database_url.get_secret_value())
    try:
        with engine.begin() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "029")
            connection.execute(
                Tenant.__table__.insert().values(
                    id=tenant,
                    slug="legacy-arm",
                    display_name="Legacy",
                    domain="legacy.test",
                    licences=[],
                    revision=1,
                )
            )
            # Reflect the historical schema; current ORM defaults include newer columns.
            users = Table("identity_users", MetaData(), autoload_with=connection)
            connection.execute(
                users.insert().values(
                    id=user,
                    tenant_id=tenant,
                    upn="reader@legacy.test",
                    display_name="Reader",
                    verifier="test-only",
                    enabled=True,
                    profile={},
                )
            )
            metadata = MetaData()
            subscriptions = Table("arm_subscriptions", metadata, autoload_with=connection)
            assignments = Table("arm_reader_assignments", metadata, autoload_with=connection)
            connection.execute(
                subscriptions.insert().values(
                    id=subscription, tenant_id=tenant, display_name="Preserved", state="Enabled"
                )
            )
            for identifier in (first, second):
                connection.execute(
                    assignments.insert().values(
                        id=identifier, tenant_id=tenant, subscription_id=subscription, user_id=user
                    )
                )
        migrate(settings.database_url.get_secret_value())
        with engine.begin() as connection:
            table = Table("arm_role_assignments", MetaData(), autoload_with=connection)
            rows = connection.execute(select(table)).mappings().all()
            assert {row["id"] for row in rows} == {first, second}
            assert all(row["scope_key"] == f"/subscriptions/{subscription}" for row in rows)
            assert all(row["role_id"] == "acdd72a7-3385-48ef-bd42-f606fba81ae7" for row in rows)
            connection.execute(table.delete().where(table.c.id == first))
            assert connection.execute(select(table.c.id)).scalars().all() == [second]
    finally:
        engine.dispose()

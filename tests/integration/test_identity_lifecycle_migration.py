from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from mockazure.db.migrations import configuration, migrate


def test_upgrade_preserves_existing_live_and_deleted_identity_state(settings):
    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    tenant, live, gone, app, registration, sp = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    with engine.begin() as connection:
        config = configuration()
        config.attributes["connection"] = connection
        command.upgrade(config, "078")
        connection.execute(
            text(
                "INSERT INTO tenants (id,slug,display_name,domain,licences,revision) "
                "VALUES (:id,'old','Old','old.test','[]',1)"
            ),
            {"id": tenant},
        )
        for uid, name, deleted in ((live, "live", False), (gone, "gone", True)):
            connection.execute(
                text(
                    "INSERT INTO identity_users "
                    "(id,tenant_id,upn,display_name,verifier,enabled,profile,deleted_at) "
                    "VALUES (:id,:tenant,:upn,:name,'unusable-retained-verifier',:enabled,'{}',"
                    "CASE WHEN :deleted THEN now() ELSE NULL END)"
                ),
                {
                    "id": uid,
                    "tenant": tenant,
                    "upn": name + "@old.test",
                    "name": name,
                    "enabled": not deleted,
                    "deleted": deleted,
                },
            )
        connection.execute(
            text(
                "INSERT INTO identity_applications "
                "(client_id,object_id,tenant_id,display_name,client_mode,redirect_uris,"
                "required_permissions,enabled,deleted_at,profile) VALUES "
                "(:id,:object,:tenant,'Old app','confidential','[]','[]',false,now(),'{}')"
            ),
            {"id": app, "object": registration, "tenant": tenant},
        )
        connection.execute(
            text(
                "INSERT INTO identity_service_principals "
                "(id,tenant_id,client_id,enabled,deleted_at,profile) "
                "VALUES (:id,:tenant,:app,false,now(),'{}')"
            ),
            {"id": sp, "tenant": tenant, "app": app},
        )
        connection.execute(
            text(
                "INSERT INTO identity_credentials "
                "(id,tenant_id,client_id,kind,verifier,expires_at) "
                "VALUES (:id,:tenant,:app,'secret','legacy-unusable-verifier',"
                "now()+interval '1 day')"
            ),
            {"id": uuid4(), "tenant": tenant, "app": app},
        )
        connection.execute(
            text(
                "INSERT INTO oauth_sessions "
                "(id,tenant_id,client_id,user_id,resource,scopes,mfa,created_at,expires_at) "
                "VALUES (:id,:tenant,:app,:user,'graph','[]','not_performed',now(),"
                "now()+interval '1 day')"
            ),
            {"id": uuid4(), "tenant": tenant, "app": app, "user": live},
        )
    migrate(settings.database_url.get_secret_value())
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM identity_users")) == 2
        row = connection.execute(
            text("SELECT object_id, kind, snapshot FROM identity_deleted_objects WHERE kind='user'")
        ).one()
        assert row.object_id == gone and row.kind == "user"
        assert row.snapshot["legacy"] is True and row.snapshot["enabled"] is False
        assert "verifier" not in row.snapshot
        assert connection.scalar(text("SELECT count(*) FROM identity_deleted_objects")) == 3
        assert (
            connection.scalar(text("SELECT revoked_at IS NOT NULL FROM identity_credentials"))
            is True
        )
        assert (
            connection.scalar(
                text("SELECT authority_valid_from IS NOT NULL FROM identity_applications")
            )
            is True
        )
        assert connection.scalar(text("SELECT revoked_at IS NOT NULL FROM oauth_sessions")) is True
        config = configuration()
        config.attributes["connection"] = connection
        with pytest.raises(Exception, match="Cannot discard"):
            command.downgrade(config, "078")
    engine.dispose()

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from mockazure.db.migrations import configuration
from mockazure.db.models import Tenant
from mockazure.mail.unified_audit_models import UnifiedAuditSession


def test_unified_audit_upgrade_preserves_data_and_refuses_lossy_downgrade(settings):
    engine = create_engine(settings.database_url.get_secret_value())
    tenant = uuid4()
    try:
        with engine.begin() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "052")
            connection.execute(
                Tenant.__table__.insert().values(
                    id=tenant,
                    slug="audit-upgrade",
                    display_name="Keep",
                    domain="audit-upgrade.test",
                    licences=[],
                    revision=9,
                )
            )
            command.upgrade(config, "head")
            assert (
                connection.scalar(text("SELECT revision FROM tenants WHERE id=:id"), {"id": tenant})
                == 9
            )
            now = datetime.now(UTC)
            connection.execute(
                UnifiedAuditSession.__table__.insert().values(
                    tenant_id=tenant,
                    id="a" * 64,
                    owner="b" * 64,
                    generation=uuid4(),
                    fingerprint="c" * 64,
                    first_mode="ReturnLargeSet",
                    mixed_mode=False,
                    position=0,
                    record_count=0,
                    created_at=now,
                    expires_at=now + timedelta(hours=1),
                )
            )
        with pytest.raises(DBAPIError, match="Cannot discard persisted audit sessions"):
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "052")
        with engine.begin() as connection:
            connection.execute(UnifiedAuditSession.__table__.delete())
            config.attributes["connection"] = connection
            command.downgrade(config, "052")
            command.upgrade(config, "head")
            assert (
                connection.scalar(text("SELECT revision FROM tenants WHERE id=:id"), {"id": tenant})
                == 9
            )
    finally:
        engine.dispose()

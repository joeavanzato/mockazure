"""Typed hunting storage, independent of arbitrary Log Analytics workspaces."""

from alembic import op

revision = "082"
down_revision = "081"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE hunting_records (
 tenant_id uuid NOT NULL REFERENCES tenants(id), table_name varchar(64) NOT NULL,
 source_id varchar(512) NOT NULL, timestamp timestamptz NOT NULL,
 account_id varchar(512), device_id varchar(512), payload jsonb NOT NULL,
 provenance jsonb NOT NULL, digest varchar(64) NOT NULL,
 PRIMARY KEY(tenant_id,table_name,source_id)
);
CREATE INDEX ix_hunting_time ON hunting_records(tenant_id,table_name,timestamp);
CREATE INDEX ix_hunting_account ON hunting_records(tenant_id,account_id,timestamp);
CREATE INDEX ix_hunting_device ON hunting_records(tenant_id,device_id,timestamp);
""")


def downgrade():
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM hunting_records)
 THEN RAISE EXCEPTION 'Cannot discard hunting records'; END IF;
END $$;
DROP TABLE hunting_records;
""")

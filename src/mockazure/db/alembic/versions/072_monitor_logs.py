"""Tenant-bound typed workspace logs."""

from alembic import op

revision = "072"
down_revision = "071"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE monitor_workspaces
ADD CONSTRAINT uq_monitor_workspace_tenant_customer UNIQUE(tenant_id,customer_id);
CREATE TABLE monitor_log_records (
id uuid PRIMARY KEY, tenant_id uuid NOT NULL, workspace_id uuid NOT NULL,
table_name varchar(80) NOT NULL, source_id varchar(512) NOT NULL, origin varchar(20) NOT NULL,
time_generated timestamptz NOT NULL, payload jsonb NOT NULL,
FOREIGN KEY(tenant_id,workspace_id)
REFERENCES monitor_workspaces(tenant_id,customer_id) ON DELETE CASCADE,
UNIQUE(tenant_id,workspace_id,table_name,source_id));
CREATE INDEX ix_monitor_log_scan
ON monitor_log_records(tenant_id,workspace_id,table_name,time_generated,id);""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM monitor_log_records)
THEN RAISE EXCEPTION 'Reset workspace logs before downgrade'; END IF; END $$;
DROP TABLE monitor_log_records;
ALTER TABLE monitor_workspaces DROP CONSTRAINT uq_monitor_workspace_tenant_customer;""")

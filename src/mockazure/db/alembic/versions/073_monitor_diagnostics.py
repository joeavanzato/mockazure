"""Diagnostic settings and generation-bound durable delivery."""

from alembic import op

revision = "073"
down_revision = "072"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE monitor_diagnostic_settings (
id uuid PRIMARY KEY,tenant_id uuid NOT NULL REFERENCES cloud_state(tenant_id) ON DELETE CASCADE,
resource_key varchar(2048) NOT NULL,resource_id varchar(2048) NOT NULL,
name varchar(260) NOT NULL,name_key varchar(260) NOT NULL,properties jsonb NOT NULL,
destinations jsonb NOT NULL,deleted boolean NOT NULL,updated_at timestamptz NOT NULL,
UNIQUE(tenant_id,id));
CREATE INDEX ix_monitor_diagnostic_settings_tenant_id ON monitor_diagnostic_settings(tenant_id);
CREATE UNIQUE INDEX uq_diagnostic_name
ON monitor_diagnostic_settings(tenant_id,resource_key,name_key) WHERE NOT deleted;
CREATE TABLE monitor_diagnostic_deliveries (
id uuid PRIMARY KEY,tenant_id uuid NOT NULL,setting_id uuid NOT NULL,source_id uuid NOT NULL,
generation integer NOT NULL,destination_kind varchar(20) NOT NULL,
destination jsonb NOT NULL,event jsonb NOT NULL,
FOREIGN KEY(tenant_id,id) REFERENCES jobs(tenant_id,id) ON DELETE CASCADE,
FOREIGN KEY(tenant_id,setting_id)
REFERENCES monitor_diagnostic_settings(tenant_id,id) ON DELETE CASCADE,
UNIQUE(tenant_id,setting_id,source_id,destination_kind));
CREATE INDEX ix_monitor_diagnostic_deliveries_tenant_id ON monitor_diagnostic_deliveries(tenant_id);
""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM monitor_diagnostic_settings)
OR EXISTS(SELECT 1 FROM monitor_diagnostic_deliveries)
THEN RAISE EXCEPTION 'Reset diagnostics before downgrade'; END IF; END $$;
DROP TABLE monitor_diagnostic_deliveries;DROP TABLE monitor_diagnostic_settings;""")

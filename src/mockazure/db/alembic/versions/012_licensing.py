"""Stored tenant inventory and direct user assignments."""

from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE licensing_skus (
 tenant_id UUID NOT NULL REFERENCES tenants(id),
 sku_id UUID NOT NULL, id UUID NOT NULL UNIQUE,
 sku_part_number VARCHAR(200) NOT NULL, status VARCHAR(32) NOT NULL,
 enabled INTEGER NOT NULL, suspended INTEGER NOT NULL, warning INTEGER NOT NULL,
 consumed_units INTEGER NOT NULL, service_plans JSONB NOT NULL,
 allowed_usage_locations JSONB NOT NULL,
 PRIMARY KEY(tenant_id,sku_id),
 CHECK(enabled >= consumed_units AND consumed_units >= 0),
 CHECK(suspended >= 0 AND warning >= 0)
);
CREATE TABLE licensing_user_assignments (
 tenant_id UUID NOT NULL, user_id UUID NOT NULL, sku_id UUID NOT NULL,
 disabled_plans JSONB NOT NULL, assigned_at TIMESTAMP WITH TIME ZONE NOT NULL,
 PRIMARY KEY(tenant_id,user_id,sku_id),
 FOREIGN KEY(tenant_id,sku_id) REFERENCES licensing_skus(tenant_id,sku_id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES identity_users(tenant_id,id)
);
CREATE INDEX ix_licensing_assignment_sku ON licensing_user_assignments(tenant_id,sku_id);
""")


def downgrade():
    raise RuntimeError("Downgrade would discard licensing state")

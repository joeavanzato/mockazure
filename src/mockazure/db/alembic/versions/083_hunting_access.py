"""Explicit local hunting service entitlement and delegated data visibility."""

from alembic import op

revision = "083"
down_revision = "082"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE hunting_settings (
 tenant_id uuid PRIMARY KEY REFERENCES tenants(id), entitlements jsonb NOT NULL DEFAULT '[]'
);
CREATE TABLE hunting_visibility (
 tenant_id uuid NOT NULL, user_id uuid NOT NULL, view_data boolean NOT NULL DEFAULT false,
 tables jsonb NOT NULL DEFAULT '[]', device_ids jsonb,
 PRIMARY KEY(tenant_id,user_id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES identity_users(tenant_id,id)
);
""")


def downgrade():
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM hunting_settings) OR EXISTS(SELECT 1 FROM hunting_visibility)
 THEN RAISE EXCEPTION 'Cannot discard hunting authorization state'; END IF;
END $$;
DROP TABLE hunting_visibility;
DROP TABLE hunting_settings;
""")

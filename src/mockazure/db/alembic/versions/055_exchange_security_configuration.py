"""Exchange security configuration and audit ingestion control."""

from alembic import op

revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE mail_security_records (
        tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        id uuid NOT NULL, kind varchar(40) NOT NULL, name_key varchar(64) NOT NULL,
        payload jsonb NOT NULL,
        PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,kind,name_key)
    );
    CREATE TABLE mail_audit_settings (
        tenant_id uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
        enabled boolean NOT NULL DEFAULT true
    );
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM mail_security_records) OR
           EXISTS(SELECT 1 FROM mail_audit_settings WHERE NOT enabled)
        THEN RAISE EXCEPTION 'Cannot discard Exchange security configuration'; END IF;
    END $$;""")
    op.execute("DROP TABLE mail_security_records; DROP TABLE mail_audit_settings;")

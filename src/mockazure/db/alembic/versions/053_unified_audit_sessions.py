"""Durable Exchange Unified Audit Log search sessions."""

from alembic import op

revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE mail_unified_audit_sessions (
            tenant_id uuid NOT NULL REFERENCES tenants(id), id varchar(64) NOT NULL,
            owner varchar(64) NOT NULL, generation uuid NOT NULL, fingerprint varchar(64) NOT NULL,
            first_mode varchar(32) NOT NULL, mixed_mode boolean NOT NULL DEFAULT false,
            position integer NOT NULL DEFAULT 0 CHECK(position >= 0),
            record_count integer NOT NULL CHECK(record_count BETWEEN 0 AND 50000),
            created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
            PRIMARY KEY(tenant_id,id));
        CREATE INDEX ix_unified_audit_expiry
            ON mail_unified_audit_sessions(tenant_id,expires_at);
        CREATE TABLE mail_unified_audit_items (
            tenant_id uuid NOT NULL, session_id varchar(64) NOT NULL,
            position integer NOT NULL CHECK(position > 0), payload jsonb NOT NULL,
            PRIMARY KEY(tenant_id,session_id,position),
            FOREIGN KEY(tenant_id,session_id) REFERENCES mail_unified_audit_sessions(tenant_id,id)
                ON DELETE CASCADE);
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM mail_unified_audit_sessions)
        THEN RAISE EXCEPTION 'Cannot discard persisted audit sessions'; END IF;
    END $$;""")
    op.execute("DROP TABLE mail_unified_audit_items")
    op.execute("DROP TABLE mail_unified_audit_sessions")

"""Explicit method policy compatibility and trusted simulation context."""

from alembic import op

revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE authentication_method_policy_state (
            tenant_id uuid PRIMARY KEY REFERENCES tenants(id),
            enforced boolean NOT NULL, configurations jsonb NOT NULL DEFAULT '{}',
            modified_at timestamptz);
        INSERT INTO authentication_method_policy_state(tenant_id,enforced)
            SELECT id,false FROM tenants;
        CREATE TABLE trusted_authentication_contexts (
            session_id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
            client_id uuid NOT NULL, user_id uuid, device_id uuid, platform varchar(32),
            method_id varchar(128), method_version varchar(36), method_completed_at timestamptz,
            expires_at timestamptz NOT NULL);
        CREATE INDEX ix_trusted_authentication_contexts_tenant_id
            ON trusted_authentication_contexts(tenant_id);
        CREATE INDEX ix_trusted_authentication_contexts_expires_at
            ON trusted_authentication_contexts(expires_at);
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM authentication_method_policy_state 
            WHERE enforced OR configurations<>'{}'::jsonb)
          OR EXISTS(SELECT 1 FROM trusted_authentication_contexts)
        THEN RAISE EXCEPTION 'Cannot downgrade persisted authentication policy state'; END IF;
    END $$;""")
    op.execute("DROP TABLE trusted_authentication_contexts")
    op.execute("DROP TABLE authentication_method_policy_state")

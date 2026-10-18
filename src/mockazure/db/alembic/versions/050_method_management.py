"""Credential epochs and durable cloud password reset operations."""

from alembic import op

revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("identity_users", "oauth_authorizations", "oauth_devices", "oauth_sessions"):
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN credential_version bigint NOT NULL DEFAULT 0 "
            "CHECK(credential_version>=0)"
        )
    op.execute(
        "ALTER TABLE identity_users ADD COLUMN password_change_required "
        "boolean NOT NULL DEFAULT false"
    )
    op.execute("""
        CREATE TABLE authentication_password_resets (
            id uuid PRIMARY KEY, tenant_id uuid NOT NULL, user_id uuid NOT NULL,
            client_id uuid NOT NULL, actor_id uuid NOT NULL,
            status varchar(24) NOT NULL
                CHECK(status IN ('notStarted','running','succeeded','failed')),
            status_detail varchar(200) NOT NULL, verifier varchar(256),
            created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
            FOREIGN KEY (tenant_id,user_id) REFERENCES identity_users(tenant_id,id));
        CREATE INDEX ix_authentication_password_resets_tenant_id
            ON authentication_password_resets(tenant_id);
        CREATE UNIQUE INDEX uq_authentication_reset_pending
            ON authentication_password_resets(tenant_id,user_id)
            WHERE status IN ('notStarted','running');
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM authentication_password_resets)
           OR EXISTS(SELECT 1 FROM identity_users
                WHERE credential_version>0 OR password_change_required)
        THEN RAISE EXCEPTION 'Cannot downgrade persisted password reset state'; END IF;
        END $$;""")
    op.execute("DROP TABLE authentication_password_resets")
    op.execute("ALTER TABLE identity_users DROP COLUMN password_change_required")
    for table in ("identity_users", "oauth_authorizations", "oauth_devices", "oauth_sessions"):
        op.execute(f"ALTER TABLE {table} DROP COLUMN credential_version")

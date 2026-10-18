"""Local Storage account credentials and service properties."""

from alembic import op

revision = "058"
down_revision = "057"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE storage_accounts (
        tenant_id uuid NOT NULL, scope_key varchar(2048) NOT NULL,
        name varchar(24) NOT NULL UNIQUE CHECK(name ~ '^[a-z0-9]{3,24}$'),
        key1 text NOT NULL, key2 text NOT NULL,
        key1_created timestamptz NOT NULL, key2_created timestamptz NOT NULL,
        services jsonb NOT NULL DEFAULT '{}',
        PRIMARY KEY(tenant_id,scope_key),
        FOREIGN KEY(tenant_id,scope_key)
            REFERENCES arm_resources(tenant_id,scope_key) ON DELETE CASCADE
    );
    """)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM storage_accounts)
        THEN RAISE EXCEPTION 'Cannot discard operational Storage accounts'; END IF; END $$;""")
    op.drop_table("storage_accounts")

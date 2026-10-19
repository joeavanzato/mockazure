"""Account-bound Blob containers and FileREST shares."""

from alembic import op

revision = "061"
down_revision = "060"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE storage_namespaces (
      tenant_id uuid NOT NULL, account_id varchar(2048) NOT NULL,
      service varchar(8) NOT NULL CHECK(service IN ('blob','file')),
      name varchar(63) NOT NULL, etag varchar(64) NOT NULL,
      created_at timestamptz NOT NULL, changed_at timestamptz NOT NULL,
      properties jsonb NOT NULL DEFAULT '{}', policies jsonb NOT NULL DEFAULT '[]',
      PRIMARY KEY(tenant_id,account_id,service,name),
      FOREIGN KEY(tenant_id,account_id) REFERENCES storage_accounts(tenant_id,scope_key)
        ON DELETE CASCADE);
      CREATE TABLE storage_delegation_keys (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, account_id varchar(2048) NOT NULL,
        object_id uuid NOT NULL, client_id uuid NOT NULL, user_id uuid,
        starts_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
        version varchar(10) NOT NULL, secret text NOT NULL,
        FOREIGN KEY(tenant_id,account_id) REFERENCES storage_accounts(tenant_id,scope_key)
        ON DELETE CASCADE,
        UNIQUE(tenant_id,account_id,object_id,starts_at,expires_at,version));
      CREATE INDEX ix_storage_delegation_keys_expires_at ON storage_delegation_keys(expires_at);
    """)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM storage_namespaces)
      OR EXISTS(SELECT 1 FROM storage_delegation_keys)
      THEN RAISE EXCEPTION 'Cannot discard Storage namespace state'; END IF; END $$;""")
    op.drop_table("storage_delegation_keys")
    op.drop_table("storage_namespaces")

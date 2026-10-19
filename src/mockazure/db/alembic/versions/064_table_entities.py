"""Typed Table entities and continuation state."""

from alembic import op

revision = "064"
down_revision = "063"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE storage_tables (
 tenant_id uuid NOT NULL,id uuid NOT NULL,account_id varchar(2048) NOT NULL,
 name varchar(63) NOT NULL,name_key varchar(63) NOT NULL,created_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,account_id,name_key),
 FOREIGN KEY(tenant_id,account_id) REFERENCES storage_accounts(tenant_id,scope_key)
 ON DELETE CASCADE);
CREATE TABLE storage_table_entities (
 tenant_id uuid NOT NULL,id uuid NOT NULL,table_id uuid NOT NULL,partition_key text NOT NULL,
 row_key text NOT NULL,partition_hash varchar(64) NOT NULL,row_hash varchar(64) NOT NULL,
 properties jsonb NOT NULL,timestamp_text varchar(32) NOT NULL,changed_at timestamptz NOT NULL,
 etag varchar(80) NOT NULL,PRIMARY KEY(tenant_id,id),
 UNIQUE(tenant_id,table_id,partition_hash,row_hash),
 FOREIGN KEY(tenant_id,table_id) REFERENCES storage_tables(tenant_id,id) ON DELETE CASCADE);
CREATE TABLE storage_table_cursors (
 tenant_id uuid NOT NULL,id uuid NOT NULL,account_id varchar(2048) NOT NULL,table_id uuid,
 generation bigint NOT NULL,query jsonb NOT NULL,last_key jsonb NOT NULL,
 expires_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,id),FOREIGN KEY(tenant_id,account_id)
 REFERENCES storage_accounts(tenant_id,scope_key)
 ON DELETE CASCADE);
CREATE INDEX storage_table_cursor_cleanup ON storage_table_cursors(tenant_id,expires_at,id);
""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM storage_tables)
 THEN RAISE EXCEPTION 'Export and reset Table state before downgrade'; END IF; END $$;
DROP TABLE storage_table_cursors,storage_table_entities,storage_tables;""")

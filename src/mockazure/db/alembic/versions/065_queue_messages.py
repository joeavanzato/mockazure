"""Durable Queue delivery and namespace continuations."""

from alembic import op

revision = "065"
down_revision = "064"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE storage_namespaces DROP CONSTRAINT storage_namespaces_service_check;
ALTER TABLE storage_namespaces ADD CONSTRAINT storage_namespaces_service_check
 CHECK(service IN ('blob','file','queue'));
CREATE TABLE storage_queue_messages (
 tenant_id uuid NOT NULL,id uuid NOT NULL,account_id varchar(2048) NOT NULL,
 service varchar(8) NOT NULL CHECK(service='queue'),queue varchar(63) NOT NULL,
 body text NOT NULL CHECK(octet_length(body)<=65536),inserted_at timestamptz NOT NULL,
 expires_at timestamptz,visible_at timestamptz NOT NULL,
 dequeue_count bigint NOT NULL CHECK(dequeue_count>=0),
 receipt_hash varchar(64),PRIMARY KEY(tenant_id,id),
 FOREIGN KEY(tenant_id,account_id,service,queue) REFERENCES storage_namespaces
 (tenant_id,account_id,service,name) ON DELETE CASCADE);
CREATE INDEX storage_queue_delivery ON storage_queue_messages
 (tenant_id,account_id,queue,visible_at,inserted_at,id);
CREATE INDEX storage_queue_expiry ON storage_queue_messages(tenant_id,expires_at,id);
CREATE TABLE storage_queue_cursors (
 tenant_id uuid NOT NULL,id uuid NOT NULL,account_id varchar(2048) NOT NULL,
 generation bigint NOT NULL,query jsonb NOT NULL,last_key varchar(63) NOT NULL,
 expires_at timestamptz NOT NULL,PRIMARY KEY(tenant_id,id),
 FOREIGN KEY(tenant_id,account_id) REFERENCES storage_accounts(tenant_id,scope_key)
 ON DELETE CASCADE);
CREATE INDEX storage_queue_cursor_cleanup ON storage_queue_cursors(tenant_id,expires_at,id);
""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM storage_namespaces WHERE service='queue')
 THEN RAISE EXCEPTION 'Export and reset Queue state before downgrade'; END IF; END $$;
DROP TABLE storage_queue_cursors,storage_queue_messages;
ALTER TABLE storage_namespaces DROP CONSTRAINT storage_namespaces_service_check;
ALTER TABLE storage_namespaces ADD CONSTRAINT storage_namespaces_service_check
 CHECK(service IN ('blob','file'));""")

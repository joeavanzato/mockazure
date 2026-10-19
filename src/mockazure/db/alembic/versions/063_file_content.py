"""FileREST live/snapshot trees and content ownership."""

from alembic import op

revision = "063"
down_revision = "062"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE storage_file_trees (
 tenant_id uuid NOT NULL,id uuid NOT NULL,account_id varchar(2048) NOT NULL,
 service varchar(8) NOT NULL DEFAULT 'file' CHECK(service='file'),share varchar(63) NOT NULL,
 stamp varchar(40) NOT NULL DEFAULT '',created_at timestamptz NOT NULL,
 properties jsonb NOT NULL DEFAULT '{}',
 PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,account_id,share,stamp),
 FOREIGN KEY(tenant_id,account_id,service,share) REFERENCES storage_namespaces
 (tenant_id,account_id,service,name) ON DELETE CASCADE);
CREATE TABLE storage_file_nodes (
 tenant_id uuid NOT NULL,id uuid NOT NULL,tree_id uuid NOT NULL,path varchar(2048) NOT NULL,
 path_key varchar(2048) NOT NULL,path_hash varchar(64) NOT NULL,parent_hash varchar(64) NOT NULL,
 directory boolean NOT NULL,public_id uuid NOT NULL,version_id uuid NOT NULL,content_id uuid,
 size bigint NOT NULL DEFAULT 0 CHECK(size BETWEEN 0 AND 268435456),
 ranges jsonb NOT NULL DEFAULT '[]',
 properties jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL,changed_at timestamptz NOT NULL,
 etag varchar(64) NOT NULL,lease_id uuid,lease_state varchar(12) NOT NULL DEFAULT 'available',
 PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,tree_id,path_hash),
 FOREIGN KEY(tenant_id,tree_id) REFERENCES storage_file_trees(tenant_id,id) ON DELETE CASCADE,
 FOREIGN KEY(tenant_id,content_id) REFERENCES content_objects(tenant_id,id),
 CHECK((directory AND content_id IS NULL AND size=0) OR (NOT directory AND content_id IS NOT NULL)),
 CHECK(lease_state IN ('available','leased','broken')));
CREATE INDEX storage_file_children ON storage_file_nodes(tenant_id,tree_id,parent_hash);
CREATE TABLE storage_file_cursors (
 tenant_id uuid NOT NULL,id uuid NOT NULL,tree_id uuid NOT NULL,generation bigint NOT NULL,
 query jsonb NOT NULL,last_key varchar(2048) NOT NULL,expires_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,id),FOREIGN KEY(tenant_id,tree_id)
 REFERENCES storage_file_trees(tenant_id,id) ON DELETE CASCADE);
CREATE INDEX storage_file_cursor_cleanup ON storage_file_cursors(tenant_id,expires_at,id);
CREATE TRIGGER release_file_node_pin AFTER DELETE ON storage_file_nodes
 FOR EACH ROW EXECUTE FUNCTION release_blob_content_pin('file-node');
""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM storage_file_trees)
 THEN RAISE EXCEPTION 'Export and reset File content before downgrade'; END IF; END $$;
DROP TABLE storage_file_cursors,storage_file_nodes,storage_file_trees;""")

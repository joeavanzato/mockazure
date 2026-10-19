"""Immutable Blob versions, staging, leases and generation-bound cursors."""

from alembic import op

revision = "062"
down_revision = "061"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE content_stream_uploads (
 tenant_id uuid NOT NULL,id uuid NOT NULL,expected_length bigint,max_size bigint NOT NULL,
 generation bigint NOT NULL,PRIMARY KEY(tenant_id,id),
 FOREIGN KEY(tenant_id,id) REFERENCES content_objects(tenant_id,id) ON DELETE CASCADE,
 CHECK(max_size>=0 AND (expected_length IS NULL OR expected_length BETWEEN 0 AND max_size)));
CREATE TABLE storage_blobs (
 tenant_id uuid NOT NULL,id uuid NOT NULL,account_id varchar(2048) NOT NULL,
 service varchar(8) NOT NULL DEFAULT 'blob' CHECK(service='blob'),container varchar(63) NOT NULL,
 name varchar(1024) NOT NULL,name_hash varchar(64) NOT NULL,current_id uuid,
 changed_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,account_id,container,name_hash),
 FOREIGN KEY(tenant_id,account_id,service,container) REFERENCES storage_namespaces(tenant_id,
 account_id,service,name) ON DELETE CASCADE);
CREATE TABLE storage_blob_versions (
 tenant_id uuid NOT NULL,id uuid NOT NULL,blob_id uuid NOT NULL,content_id uuid NOT NULL,
 stamp varchar(40) NOT NULL,kind varchar(12) NOT NULL CHECK(kind IN ('version','snapshot')),
 etag varchar(64) NOT NULL,size bigint NOT NULL CHECK(size>=0),created_at timestamptz NOT NULL,
 deleted_at timestamptz,deleted_until timestamptz,properties jsonb NOT NULL DEFAULT '{}',
 blocks jsonb NOT NULL DEFAULT '[]',
 PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,blob_id,stamp,kind),
 FOREIGN KEY(tenant_id,blob_id) REFERENCES storage_blobs(tenant_id,id) ON DELETE CASCADE,
 FOREIGN KEY(tenant_id,content_id) REFERENCES content_objects(tenant_id,id));
CREATE INDEX storage_blob_version_cleanup ON storage_blob_versions(tenant_id,deleted_until,id);
CREATE TABLE storage_blob_blocks (
 tenant_id uuid NOT NULL,id uuid NOT NULL,blob_id uuid NOT NULL,block_id varchar(88) NOT NULL,
 content_id uuid NOT NULL,size bigint NOT NULL CHECK(size>=0),created_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,blob_id,block_id),
 FOREIGN KEY(tenant_id,blob_id) REFERENCES storage_blobs(tenant_id,id) ON DELETE CASCADE,
 FOREIGN KEY(tenant_id,content_id) REFERENCES content_objects(tenant_id,id));
CREATE INDEX storage_blob_block_cleanup ON storage_blob_blocks(tenant_id,created_at,id);
CREATE TABLE storage_blob_leases (
 tenant_id uuid NOT NULL,blob_id uuid NOT NULL,lease_id uuid NOT NULL,duration integer NOT NULL,
 expires_at timestamptz,break_at timestamptz,etag varchar(64) NOT NULL,PRIMARY KEY(tenant_id,
 blob_id),
 FOREIGN KEY(tenant_id,blob_id) REFERENCES storage_blobs(tenant_id,id) ON DELETE CASCADE,
 CHECK(duration=-1 OR duration BETWEEN 15 AND 60));
CREATE TABLE storage_blob_cursors (
 tenant_id uuid NOT NULL,id uuid NOT NULL,account_id varchar(2048) NOT NULL,
 service varchar(8) NOT NULL DEFAULT 'blob' CHECK(service='blob'),container varchar(63) NOT NULL,
 generation bigint NOT NULL,query jsonb NOT NULL,last_key jsonb NOT NULL,
 expires_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,id),FOREIGN KEY(tenant_id,account_id,service,container)
 REFERENCES storage_namespaces(tenant_id,account_id,service,name) ON DELETE CASCADE);
CREATE INDEX storage_blob_cursor_cleanup ON storage_blob_cursors(tenant_id,expires_at,id);
CREATE FUNCTION release_blob_content_pin() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN DELETE FROM content_references WHERE tenant_id=OLD.tenant_id AND owner_id=OLD.id::text
 AND owner_kind=TG_ARGV[0]; RETURN OLD; END $$;
CREATE TRIGGER release_blob_version_pin AFTER DELETE ON storage_blob_versions
 FOR EACH ROW EXECUTE FUNCTION release_blob_content_pin('blob-version');
CREATE TRIGGER release_blob_block_pin AFTER DELETE ON storage_blob_blocks
 FOR EACH ROW EXECUTE FUNCTION release_blob_content_pin('blob-block');
""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM storage_blobs) OR EXISTS(SELECT 1 FROM
 content_stream_uploads)
 THEN RAISE EXCEPTION 'Export and reset Blob content before downgrade'; END IF; END $$;
DROP TABLE storage_blob_cursors,storage_blob_leases,storage_blob_blocks,storage_blob_versions,
 storage_blobs,content_stream_uploads;
DROP FUNCTION release_blob_content_pin();""")

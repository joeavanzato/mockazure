"""Durable tenant-local binary evidence, staged chunks and reference retention."""

from alembic import op

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE content_objects (
 tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
 id uuid NOT NULL, size bigint NOT NULL CHECK(size >= 0), sha256 varchar(64) NOT NULL,
 media_type varchar(255) NOT NULL, state varchar(12) NOT NULL DEFAULT 'staged'
 CHECK(state IN ('staged','finalized','alias')), canonical_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,id)
);
CREATE UNIQUE INDEX content_finalized_hash ON content_objects(tenant_id,sha256,media_type)
 WHERE state='finalized';
CREATE INDEX content_cleanup ON content_objects(tenant_id,created_at,id);
CREATE TABLE content_chunks (
 tenant_id uuid NOT NULL, content_id uuid NOT NULL, ordinal integer NOT NULL CHECK(ordinal >= 0),
 data bytea NOT NULL CHECK(octet_length(data) BETWEEN 1 AND 1048576),
 PRIMARY KEY(tenant_id,content_id,ordinal), FOREIGN KEY(tenant_id,content_id)
 REFERENCES content_objects(tenant_id,id) ON DELETE CASCADE
);
CREATE TABLE content_references (
 tenant_id uuid NOT NULL, content_id uuid NOT NULL, owner_kind varchar(64) NOT NULL,
 owner_id varchar(512) NOT NULL, PRIMARY KEY(tenant_id,content_id,owner_kind,owner_id),
 FOREIGN KEY(tenant_id,content_id) REFERENCES content_objects(tenant_id,id)
);
CREATE INDEX content_reference_owner ON content_references(tenant_id,owner_kind,owner_id);
""")


def downgrade():
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM content_objects) THEN
 RAISE EXCEPTION 'Export and reset content before downgrade';
 END IF;
END $$;
DROP TABLE content_references,content_chunks,content_objects;
""")

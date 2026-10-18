"""Shared versioned workload evidence and separate resource grants."""

from alembic import op

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE workload_scopes (
 tenant_id uuid NOT NULL REFERENCES tenants(id),workload varchar(32) NOT NULL,
 scope varchar(512) NOT NULL,
 revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0),generation uuid NOT NULL,
 grant_revision bigint NOT NULL DEFAULT 0,PRIMARY KEY(tenant_id,workload,scope)
);
CREATE TABLE workload_versions (
 tenant_id uuid NOT NULL,workload varchar(32) NOT NULL,scope varchar(512) NOT NULL,
 kind varchar(64) NOT NULL,id varchar(512) NOT NULL,valid_from bigint NOT NULL,valid_to bigint,
 deleted boolean NOT NULL DEFAULT false,payload jsonb NOT NULL,content_id uuid,name varchar(512),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,workload,scope,kind,id,valid_from),
 FOREIGN KEY(tenant_id,workload,scope) REFERENCES workload_scopes(tenant_id,workload,scope)
);
CREATE UNIQUE INDEX workload_current_identity ON workload_versions(tenant_id,workload,kind,
 id) WHERE valid_to IS NULL;
CREATE INDEX workload_snapshot ON workload_versions(tenant_id,workload,scope,kind,valid_from,
 valid_to,id);
CREATE INDEX workload_names ON workload_versions(tenant_id,workload,scope,kind,lower(name));
CREATE TABLE workload_relations (
 tenant_id uuid NOT NULL,workload varchar(32) NOT NULL,scope varchar(512) NOT NULL,
 source_kind varchar(64) NOT NULL,source_id varchar(512) NOT NULL,relation varchar(64) NOT NULL,
 target_kind varchar(64) NOT NULL,target_id varchar(512) NOT NULL,valid_from bigint NOT NULL,
 valid_to bigint,
 PRIMARY KEY(tenant_id,workload,scope,source_kind,source_id,relation,target_kind,target_id,
 valid_from),
 FOREIGN KEY(tenant_id,workload,scope) REFERENCES workload_scopes(tenant_id,workload,scope)
);
CREATE UNIQUE INDEX workload_current_edge ON workload_relations(tenant_id,workload,scope,
 source_kind,source_id,relation,target_kind,target_id) WHERE valid_to IS NULL;
CREATE INDEX workload_parent ON workload_relations(tenant_id,workload,target_kind,target_id,
 valid_to);
CREATE TABLE workload_grants (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL,workload varchar(32) NOT NULL,
 scope varchar(512) NOT NULL,
 principal_kind varchar(16) NOT NULL CHECK(principal_kind IN ('user','application')),
 principal_id uuid NOT NULL,role varchar(16) NOT NULL CHECK(role IN ('read','write','owner',
 'fullcontrol')),
 display_name varchar(256),UNIQUE(tenant_id,workload,scope,principal_kind,principal_id),
 FOREIGN KEY(tenant_id,workload,scope) REFERENCES workload_scopes(tenant_id,workload,scope)
);
CREATE TABLE workload_cursors (
 token_hash varchar(64) PRIMARY KEY,tenant_id uuid NOT NULL REFERENCES tenants(id),
 workload varchar(32) NOT NULL,
 state jsonb NOT NULL,expires_at timestamptz NOT NULL
);
CREATE INDEX workload_cursor_expiry ON workload_cursors(tenant_id,workload,expires_at);
CREATE TABLE workload_downloads (
 token_hash varchar(64) PRIMARY KEY,tenant_id uuid NOT NULL REFERENCES tenants(id),
 content_id uuid NOT NULL,expires_at timestamptz NOT NULL
);
CREATE INDEX workload_download_expiry ON workload_downloads(tenant_id,expires_at);
""")


def downgrade():
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM workload_scopes) OR EXISTS(SELECT 1 FROM workload_downloads) THEN
 RAISE EXCEPTION 'Export and clear workload evidence before downgrade';
 END IF;
END $$;
DROP TABLE workload_downloads,workload_cursors,workload_grants,workload_relations,
 workload_versions,workload_scopes;
""")

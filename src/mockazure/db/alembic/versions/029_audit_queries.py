"""Durable UAL queries, immutable results and distinct Purview roles."""

from alembic import op

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE audit_roles (
 tenant_id UUID NOT NULL, user_id UUID NOT NULL, role VARCHAR(32) NOT NULL,
 PRIMARY KEY(tenant_id,user_id,role),
 FOREIGN KEY(tenant_id,user_id) REFERENCES identity_users(tenant_id,id),
 CHECK(role IN ('Audit Logs','View-Only Audit Logs'))
);
CREATE TABLE audit_queries (
 tenant_id UUID NOT NULL REFERENCES tenants(id), id UUID NOT NULL,
 job_id UUID NOT NULL UNIQUE REFERENCES jobs(id), generation UUID NOT NULL,
 revision BIGINT NOT NULL, evidence_time TIMESTAMPTZ NOT NULL,
 retention_cutoff TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL, filters JSONB NOT NULL, services JSONB NOT NULL,
 record_count BIGINT, PRIMARY KEY(tenant_id,id)
);
CREATE INDEX audit_query_list_idx ON audit_queries(tenant_id,created_at,id);
CREATE TABLE audit_query_items (
 tenant_id UUID NOT NULL, query_id UUID NOT NULL, record_id UUID NOT NULL,
 event_time TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL,
 PRIMARY KEY(tenant_id,query_id,record_id),
 FOREIGN KEY(tenant_id,query_id) REFERENCES audit_queries(tenant_id,id) ON DELETE CASCADE
);
CREATE INDEX audit_query_page_idx ON audit_query_items(tenant_id,query_id,event_time,record_id);
""")


def downgrade():
    op.execute("DROP TABLE audit_query_items, audit_queries, audit_roles")

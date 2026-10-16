"""Shared Graph budgets and explicit one-shot read faults."""

from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE graph_request_budgets (
 tenant_id uuid NOT NULL REFERENCES tenants(id), client_id uuid NOT NULL,
 resource varchar(16) NOT NULL CHECK(resource IN ('users','groups')),
 window_start timestamptz NOT NULL, used integer NOT NULL CHECK(used>0),
 PRIMARY KEY(tenant_id,client_id,resource));
CREATE TABLE graph_read_faults (
 tenant_id uuid NOT NULL REFERENCES tenants(id), client_id uuid NOT NULL,
 resource varchar(16) NOT NULL CHECK(resource IN ('users','groups')),
 kind varchar(32) NOT NULL CHECK(kind='next_read_429'),
 retry_after integer NOT NULL CHECK(retry_after BETWEEN 1 AND 300),
 PRIMARY KEY(tenant_id,client_id,resource));
""")


def downgrade():
    op.execute("DROP TABLE graph_read_faults; DROP TABLE graph_request_budgets;")

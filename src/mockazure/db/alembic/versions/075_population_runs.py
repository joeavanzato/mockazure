"""Frozen lab profiles and fenced resumable population runs."""

from alembic import op

revision = "075"
down_revision = "074"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE population_bundle_heads (id uuid PRIMARY KEY, revision integer NOT NULL);
CREATE TABLE population_bundle_versions (id uuid NOT NULL REFERENCES population_bundle_heads(id),
 revision integer NOT NULL, digest varchar(64) NOT NULL, encrypted text NOT NULL, PRIMARY
KEY(id,revision));
CREATE TABLE population_previews (id uuid PRIMARY KEY, tenant_id uuid NOT NULL, digest
varchar(64) NOT NULL,
 state_digest varchar(64) NOT NULL, encrypted text NOT NULL, expires_at timestamptz NOT NULL,
run_id uuid);
CREATE TABLE population_runs (id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES
tenants(id) ON DELETE CASCADE,
 job_id uuid NOT NULL REFERENCES jobs(id), namespace varchar(128) NOT NULL, state varchar(24)
NOT NULL,
 resolved_digest varchar(64) NOT NULL, encrypted text NOT NULL, cursor integer NOT NULL DEFAULT 0,
 cancel_requested boolean NOT NULL DEFAULT false, progress jsonb NOT NULL, errors jsonb NOT NULL,
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL);
CREATE UNIQUE INDEX uq_population_active_tenant ON population_runs(tenant_id) WHERE state IN
('pending','running','verifying');
CREATE TABLE population_steps (run_id uuid NOT NULL REFERENCES population_runs(id) ON DELETE
CASCADE,
 ordinal integer NOT NULL, key varchar(128) NOT NULL, family varchar(128) NOT NULL, count
integer NOT NULL,
 "offset" integer NOT NULL DEFAULT 0, state varchar(24) NOT NULL, PRIMARY KEY(run_id,ordinal));
CREATE TABLE population_bindings (tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
 namespace varchar(128) NOT NULL, key varchar(128) NOT NULL, actual_id text NOT NULL, PRIMARY
KEY(tenant_id,namespace,key));
CREATE TABLE population_receipts (tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
 namespace varchar(128) NOT NULL, key varchar(64) NOT NULL, family varchar(128) NOT NULL,
 input_digest varchar(64) NOT NULL, state varchar(24) NOT NULL, encrypted_result text,
 origin_run uuid NOT NULL, updated_at timestamptz NOT NULL, PRIMARY KEY(tenant_id,namespace,key));
""")


def downgrade():
    for table in (
        "population_receipts",
        "population_bindings",
        "population_steps",
        "population_runs",
        "population_previews",
        "population_bundle_versions",
        "population_bundle_heads",
    ):
        op.execute("DROP TABLE " + table)

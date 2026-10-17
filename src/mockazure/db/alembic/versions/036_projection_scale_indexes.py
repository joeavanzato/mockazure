"""Bound source-generation lookup and ordered claims for large evidence queues."""

from alembic import op

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE INDEX ix_evidence_source_key ON evidence_sources
 (tenant_id, ((kind || ':'::varchar) || id::varchar));
DROP INDEX ix_jobs_claim;
CREATE INDEX ix_jobs_claim ON jobs (state, available_at, id);
""")


def downgrade():
    op.drop_index("ix_evidence_source_key", table_name="evidence_sources")
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.create_index("ix_jobs_claim", "jobs", ["state", "available_at"])

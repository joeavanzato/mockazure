"""Keep ordered pending/reclaimed claims independent of completed queue size."""

from alembic import op

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE INDEX ix_jobs_ready_order ON jobs (available_at, id)
 WHERE state IN ('pending', 'running') AND attempts < 3;
""")


def downgrade():
    op.drop_index("ix_jobs_ready_order", table_name="jobs")

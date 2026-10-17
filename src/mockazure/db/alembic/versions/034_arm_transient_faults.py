"""Extend scoped ARM retry probes to transient service failures."""

from alembic import op

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE arm_read_faults DROP CONSTRAINT arm_read_faults_kind_check;
ALTER TABLE arm_read_faults ADD CONSTRAINT arm_read_faults_kind_check
 CHECK (kind IN ('next_read_429','next_page_429','next_read_503','next_page_503'));
""")


def downgrade():
    op.execute("""
DELETE FROM arm_read_faults WHERE kind IN ('next_read_503','next_page_503');
ALTER TABLE arm_read_faults DROP CONSTRAINT arm_read_faults_kind_check;
ALTER TABLE arm_read_faults ADD CONSTRAINT arm_read_faults_kind_check
 CHECK (kind IN ('next_read_429','next_page_429'));
""")

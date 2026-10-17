"""A persisted fault that waits for a Graph continuation request."""

from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE graph_read_faults DROP CONSTRAINT graph_read_faults_kind_check;
ALTER TABLE graph_read_faults ADD CONSTRAINT graph_read_faults_kind_check
 CHECK(kind IN ('next_read_429','next_page_429'));
""")


def downgrade():
    op.execute("""
DELETE FROM graph_read_faults WHERE kind='next_page_429';
ALTER TABLE graph_read_faults DROP CONSTRAINT graph_read_faults_kind_check;
ALTER TABLE graph_read_faults ADD CONSTRAINT graph_read_faults_kind_check
 CHECK(kind='next_read_429');
""")
